"""ForensicEngine - the orchestrator that wires everything together.

Responsibilities:
  * Load data into an in-memory, READ-ONLY DuckDB view.
  * Build the schema catalog for prompt injection.
  * Select cloud vs local implementations from :class:`Settings`.
  * For each query: route -> generate SQL -> guardrail-validate -> execute
    (with a heuristic fallback) OR run semantic search -> narrate -> visualize.

The engine has no Streamlit dependency, so it can be driven from the app, the
eval harness, tests, or a REST API equally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from .config import Settings, load_settings
from .embeddings import Embedder, build_embedder
from .narrative import LLMNarrator, Narrator, TemplateNarrator
from .router import QueryRouter, RouteDecision
from .schema import SchemaCatalog, build_catalog
from .sql.generator import HeuristicSQLGenerator, LLMSQLGenerator, SQLGenerator
from .sql.guardrails import SQLGuardrail
from .vector.base import VectorStore
from .vector.memory_store import InMemoryVectorStore

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    success: bool
    query: str
    decision: RouteDecision | None = None
    sql: str | None = None
    data: pd.DataFrame = field(default_factory=pd.DataFrame)
    narrative: str = ""
    chart: object | None = None
    warning: str | None = None
    error: str | None = None

    @property
    def rows(self) -> int:
        return len(self.data)


class ForensicEngine:
    def __init__(self, df: pd.DataFrame, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.df = df

        # In-memory analytical DB. Registering a view over the DataFrame is far
        # faster than pandas for group-bys and joins.
        self.db = duckdb.connect(":memory:")
        self.db.register("ipdr_logs", df)
        # Engine-level backstop behind the SQL guardrail: disable ALL external
        # access (ATTACH, COPY, read_csv/read_parquet, extension install/load,
        # httpfs). Even if a malicious query slipped past the guardrail it could
        # not touch the filesystem or network. In-memory views are unaffected.
        try:
            self.db.execute("SET enable_external_access=false;")
        except Exception as e:  # pragma: no cover - very old DuckDB w/o the flag
            logger.warning("Could not disable DuckDB external access: %s", e)

        self.catalog: SchemaCatalog = build_catalog(df)
        self.guardrail = SQLGuardrail(
            allowed_tables=self.settings.allowed_tables,
            default_limit=self.settings.default_row_limit,
            max_limit=self.settings.max_row_limit,
        )

        self.provider = self._build_provider()
        self.router = QueryRouter(provider=self.provider)
        self.sql_generator: SQLGenerator = self._build_sql_generator()
        self.heuristic_generator = HeuristicSQLGenerator()
        self.narrator: Narrator = self._build_narrator()

        # Semantic search components are built lazily (embedding model load is
        # expensive and not every session uses RAG).
        self._embedder: Embedder | None = None
        self._vector_store: VectorStore | None = None
        self.mode_label = self._mode_label()
        logger.info("ForensicEngine ready in %s mode", self.mode_label)

    # -- construction helpers -----------------------------------------
    def _build_provider(self):
        if not self.settings.use_cloud:
            return None
        try:
            from .llm.openai_provider import OpenAIProvider

            return OpenAIProvider(self.settings.openai_api_key,
                                  self.settings.sql_model)
        except Exception as e:
            logger.warning("OpenAI provider unavailable, falling back to local: %s", e)
            return None

    def _build_sql_generator(self) -> SQLGenerator:
        if self.provider is not None:
            return LLMSQLGenerator(self.provider)
        return HeuristicSQLGenerator()

    def _build_narrator(self) -> Narrator:
        if self.provider is not None:
            return LLMNarrator(self.provider)
        return TemplateNarrator()

    def _mode_label(self) -> str:
        return "cloud" if self.provider is not None else "local"

    # -- semantic search (lazy) ---------------------------------------
    def _ensure_vector_index(self) -> None:
        if self._vector_store is not None:
            return
        self._embedder = build_embedder(self.settings.embedding_model)
        texts = self.df.get("rag_text", pd.Series([], dtype=str)).astype(str).tolist()
        vectors = self._embedder.encode(texts)
        payloads = self.df.astype({"timestamp": str}).to_dict("records")

        store: VectorStore
        if self.settings.use_qdrant:
            try:
                from .vector.qdrant_store import QdrantVectorStore

                store = QdrantVectorStore(
                    self.settings.qdrant_url, self.settings.qdrant_api_key,
                    self.settings.qdrant_collection, self._embedder.dim,
                )
                store.ensure_collection()
            except Exception as e:
                logger.warning("Qdrant unavailable, using in-memory store: %s", e)
                store = InMemoryVectorStore()
        else:
            store = InMemoryVectorStore()

        store.upsert(list(range(len(payloads))), vectors, payloads)
        self._vector_store = store

    # -- public API ----------------------------------------------------
    def answer(self, query: str) -> QueryResult:
        decision = self.router.route(query)

        if decision.strategy in ("sql", "hybrid"):
            result = self._answer_sql(query, decision)
            if not result.success and decision.strategy == "hybrid":
                return self._answer_rag(query, decision)
            return result
        return self._answer_rag(query, decision)

    def semantic_search(self, query: str, limit: int = 30) -> pd.DataFrame:
        """Run pure semantic retrieval (bypasses routing).

        Exposed so the evaluation harness can measure retrieval relevance
        independently of the router's strategy choice.
        """
        self._ensure_vector_index()
        qvec = self._embedder.encode_one(query)
        hits = self._vector_store.search(qvec, limit=limit)
        return pd.DataFrame([h.payload for h in hits])

    # -- SQL path ------------------------------------------------------
    def _answer_sql(self, query: str, decision: RouteDecision) -> QueryResult:
        warning = None
        try:
            raw_sql = self.sql_generator.generate(query, self.catalog)
            validated = self.guardrail.validate(raw_sql)
            data = self.db.execute(validated.sql).fetchdf()
            sql = validated.sql
        except Exception as e:  # noqa: BLE001 - intentional broad degradation:
            # any failure in LLM generation, guardrail validation, or execution
            # (GuardrailError, duckdb.Error, provider errors) falls back to the
            # deterministic heuristic generator below.
            logger.info("Primary SQL failed (%s); trying heuristic generator", e)
            try:
                raw_sql = self.heuristic_generator.generate(query, self.catalog)
                validated = self.guardrail.validate(raw_sql)
                data = self.db.execute(validated.sql).fetchdf()
                sql = validated.sql
                warning = f"Primary SQL generation failed; used deterministic fallback. ({e})"
            except Exception as e2:  # noqa: BLE001
                return QueryResult(success=False, query=query, decision=decision,
                                   error=f"SQL execution failed: {e2}")

        narrative = self.narrator.narrate(query, sql, data, decision)
        chart = self._maybe_chart(data, decision, query)
        return QueryResult(success=True, query=query, decision=decision, sql=sql,
                           data=data, narrative=narrative, chart=chart,
                           warning=warning)

    # -- RAG path ------------------------------------------------------
    def _answer_rag(self, query: str, decision: RouteDecision) -> QueryResult:
        try:
            self._ensure_vector_index()
            qvec = self._embedder.encode_one(query)
            hits = self._vector_store.search(qvec, limit=30)
            data = pd.DataFrame([h.payload for h in hits])
        except Exception as e:  # noqa: BLE001
            # If semantic search is unavailable, degrade to SQL.
            logger.info("RAG failed (%s); degrading to SQL", e)
            fallback = self._answer_sql(query, decision)
            fallback.warning = (fallback.warning or "") + \
                " Semantic search unavailable; answered with SQL."
            return fallback

        narrative = self.narrator.narrate(query, "Semantic vector search", data, decision)
        chart = self._maybe_chart(data, decision, query)
        return QueryResult(success=True, query=query, decision=decision,
                           sql="Semantic vector search", data=data,
                           narrative=narrative, chart=chart)

    # -- viz helper ----------------------------------------------------
    def _maybe_chart(self, data: pd.DataFrame, decision: RouteDecision, query: str):
        if decision.visualization in ("none", "table") or data.empty:
            return None
        from .viz import create_figure

        return create_figure(data, decision.visualization, query)


def load_engine(settings: Settings | None = None) -> ForensicEngine:
    """Convenience loader: read the configured data file and build the engine."""
    settings = settings or load_settings()
    path = settings.data_path
    if str(path).endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in ("source_ip", "destination_ip", "destination_domain",
                "activity", "data_type", "protocol"):
        if col in df.columns:
            df[col] = df[col].astype(str)
    return ForensicEngine(df, settings)

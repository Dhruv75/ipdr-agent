"""Query router / orchestrator.

Decides *how* to answer a question:
  * strategy      - sql | rag | hybrid
  * query_type    - aggregation | timeseries | filter | lookup | pattern | correlation
  * visualization - bar | line | pie | table | none

Design choice: routing is **deterministic-first**. A rule-based scorer handles
the vast majority of queries with zero latency and full reproducibility. The
LLM classifier is only consulted as a tie-breaker when the rules are not
confident (and only if a provider is configured). This is cheaper, faster, and
far easier to test than routing every query through an LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict

from .llm.base import LLMProvider


@dataclass
class RouteDecision:
    strategy: str
    query_type: str
    visualization: str
    reasoning: str
    confidence: float
    source: str  # "rules" or "llm"

    def as_dict(self) -> dict:
        return asdict(self)


# Keyword -> signal tables. Kept explicit so behaviour is auditable/testable.
_RAG_SIGNALS = ("similar", "anomaly", "beaconing", "suspicious", "unusual",
                "behaviour", "behavior", "pattern like", "resembl", "looks like")
_AGG_SIGNALS = ("top", "most", "count", "how many", "distribution", "breakdown",
                "compare", "per ", "each ")
_TS_SIGNALS = ("hourly", "daily", "trend", "over time", "timeline", "per hour",
               "per day")
_LOOKUP_SIGNALS = ("first", "last", "earliest", "latest")
_CORR_SIGNALS = ("after", "followed by", "correlat", "relationship", "then visited")

_VIZ_KEYWORDS = {"bar chart": "bar", "line chart": "line", "pie": "pie",
                 "table": "table"}


class QueryRouter:
    def __init__(self, provider: LLMProvider | None = None,
                 llm_confidence_threshold: float = 0.5):
        self.provider = provider
        self.threshold = llm_confidence_threshold

    def route(self, query: str) -> RouteDecision:
        decision = self._route_by_rules(query)
        if decision.confidence < self.threshold and self.provider is not None:
            llm_decision = self._route_by_llm(query)
            if llm_decision is not None:
                return llm_decision
        return decision

    # -- deterministic scorer -----------------------------------------
    def _route_by_rules(self, query: str) -> RouteDecision:
        q = query.lower()

        def hits(signals) -> int:
            return sum(1 for s in signals if s in q)

        rag, agg, ts, lookup, corr = (
            hits(_RAG_SIGNALS), hits(_AGG_SIGNALS), hits(_TS_SIGNALS),
            hits(_LOOKUP_SIGNALS), hits(_CORR_SIGNALS),
        )

        # Strategy
        if corr and (rag or "semantic" in q):
            strategy, reason = "hybrid", "correlation needing structured + semantic steps"
        elif rag and not (agg or ts):
            strategy, reason = "rag", "behavioural/semantic similarity request"
        else:
            strategy, reason = "sql", "structured aggregation/filter/lookup"

        # Query type
        if ts:
            query_type = "timeseries"
        elif corr:
            query_type = "correlation"
        elif lookup:
            query_type = "lookup"
        elif agg:
            query_type = "aggregation"
        elif rag:
            query_type = "pattern"
        else:
            query_type = "filter"

        # Visualization
        viz = next((v for k, v in _VIZ_KEYWORDS.items() if k in q), None)
        if viz is None:
            viz = {
                "aggregation": "bar",
                "timeseries": "line",
                "pattern": "table",
                "correlation": "table",
                "lookup": "none",
                "filter": "table",
            }[query_type]

        # Confidence: strong signal count -> high confidence.
        signal_total = rag + agg + ts + lookup + corr
        confidence = min(0.5 + 0.2 * signal_total, 0.95) if signal_total else 0.4

        return RouteDecision(strategy, query_type, viz, reason, confidence, "rules")

    # -- optional LLM tie-breaker -------------------------------------
    def _route_by_llm(self, query: str) -> RouteDecision | None:
        system = (
            "Classify a network-forensics question. Respond with STRICT JSON:\n"
            '{"strategy":"sql|rag|hybrid","query_type":"aggregation|timeseries|'
            'filter|lookup|pattern|correlation","visualization":"bar|line|pie|'
            'table|none","reasoning":"short"}'
        )
        try:
            raw = self.provider.chat(system=system, user=query, json_mode=True,
                                     temperature=0.0)
            data = json.loads(raw)
            return RouteDecision(
                strategy=data.get("strategy", "sql"),
                query_type=data.get("query_type", "aggregation"),
                visualization=data.get("visualization", "none"),
                reasoning=data.get("reasoning", "llm classification"),
                confidence=0.9,
                source="llm",
            )
        except Exception:
            return None

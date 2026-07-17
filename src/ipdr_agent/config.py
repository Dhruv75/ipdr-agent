"""Central configuration.

All runtime settings are resolved here from environment variables (optionally
loaded from a local ``.env`` file). Nothing else in the codebase should read
``os.environ`` directly - that keeps configuration testable and documented in
one place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

try:  # optional: load a .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


class Mode(str, Enum):
    """Execution mode for the engine.

    * ``AUTO``  - use cloud (OpenAI/Qdrant) when credentials exist, else local.
    * ``CLOUD`` - force the cloud path (fails loudly if keys are missing).
    * ``LOCAL`` - force the fully offline path (no paid API calls at all).
    """

    AUTO = "auto"
    CLOUD = "cloud"
    LOCAL = "local"


def _project_root() -> Path:
    # config.py -> ipdr_agent -> src -> <root>
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    mode: Mode = Mode.AUTO

    # --- OpenAI ---
    openai_api_key: str = ""
    router_model: str = "gpt-4o-mini"      # cheap model for classification
    sql_model: str = "gpt-4o"              # stronger model for SQL + narrative
    narrative_model: str = "gpt-4o"

    # --- Qdrant ---
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "ipdr_logs"

    # --- Embeddings ---
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # --- Data ---
    data_path: Path = field(default_factory=lambda: _project_root() / "data" / "rag_formatted_data.xlsx")

    # --- Query safety ---
    default_row_limit: int = 100
    max_row_limit: int = 1000
    allowed_tables: tuple[str, ...] = ("ipdr_logs",)

    # ------------------------------------------------------------------
    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_qdrant(self) -> bool:
        return bool(self.qdrant_url and self.qdrant_api_key)

    @property
    def use_cloud(self) -> bool:
        """Whether the cloud LLM path should be used at all."""
        if self.mode is Mode.LOCAL:
            return False
        if self.mode is Mode.CLOUD:
            return True
        return self.has_openai  # AUTO

    @property
    def use_qdrant(self) -> bool:
        if self.mode is Mode.LOCAL:
            return False
        return self.has_qdrant


def load_settings() -> Settings:
    """Build :class:`Settings` from the environment.

    Supports both bare environment variables and Streamlit secrets (the app
    layer copies ``st.secrets`` into the environment before calling this).
    """
    mode_raw = os.getenv("IPDR_MODE", "auto").lower()
    try:
        mode = Mode(mode_raw)
    except ValueError:
        mode = Mode.AUTO

    data_path = os.getenv("IPDR_DATA_PATH")
    kwargs = dict(
        mode=mode,
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        router_model=os.getenv("IPDR_ROUTER_MODEL", "gpt-4o-mini"),
        sql_model=os.getenv("IPDR_SQL_MODEL", "gpt-4o"),
        narrative_model=os.getenv("IPDR_NARRATIVE_MODEL", "gpt-4o"),
        qdrant_url=os.getenv("QDRANT_URL", ""),
        qdrant_api_key=os.getenv("QDRANT_API_KEY", ""),
        qdrant_collection=os.getenv("QDRANT_COLLECTION", "ipdr_logs"),
        embedding_model=os.getenv("IPDR_EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    )
    if data_path:
        kwargs["data_path"] = Path(data_path)
    return Settings(**kwargs)

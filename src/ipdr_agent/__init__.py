"""IPDR Forensic Agent - hybrid Text-to-SQL + semantic RAG over network logs."""
from __future__ import annotations

from .config import Mode, Settings, load_settings
from .engine import ForensicEngine, QueryResult, load_engine

__version__ = "5.0.0"
__all__ = [
    "Mode",
    "Settings",
    "load_settings",
    "ForensicEngine",
    "QueryResult",
    "load_engine",
    "__version__",
]

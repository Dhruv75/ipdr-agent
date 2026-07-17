"""NL-to-SQL generation.

Two interchangeable implementations behind one protocol:

* LLMSQLGenerator       - few-shot prompted GPT-4o with dynamic catalog
                          injection. Used on the cloud path.
* HeuristicSQLGenerator - deterministic, rule-based translation of the common
                          query shapes. Used offline (no paid keys) and as a
                          fallback if the LLM output fails the guardrail.
"""
from __future__ import annotations

import re
from typing import Protocol

from ..llm.base import LLMProvider
from ..schema import SchemaCatalog
from .examples import FEW_SHOT_EXAMPLES, render_few_shot

_SYSTEM_TEMPLATE = """You are an expert analytics engineer that writes DuckDB SQL.

You translate a natural-language question into ONE read-only DuckDB SELECT query
over the table described below. Return ONLY SQL - no prose, no markdown fences.

{schema}

HARD RULES:
1. Output exactly one SELECT (or WITH ... SELECT) statement. Never write to data.
2. Use ONLY the columns and literal values listed in the catalog above.
3. Always include a LIMIT (default 100, never above 1000).
4. Time bucketing uses DATE_TRUNC('hour'|'day'|'week', timestamp).
5. Compare dates with CAST(timestamp AS DATE) = 'YYYY-MM-DD'.
6. String literals use single quotes; column names are case-sensitive.

Here are worked examples for this exact schema:

{few_shot}
"""


class SQLGenerator(Protocol):
    def generate(self, question: str, catalog: SchemaCatalog) -> str: ...


class LLMSQLGenerator:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def generate(self, question: str, catalog: SchemaCatalog) -> str:
        system = _SYSTEM_TEMPLATE.format(
            schema=catalog.to_prompt(),
            few_shot=render_few_shot(FEW_SHOT_EXAMPLES),
        )
        raw = self.provider.chat(system=system, user=question, temperature=0.0)
        return raw.strip()


class HeuristicSQLGenerator:
    """Offline, deterministic NL-to-SQL for the common forensic query shapes.

    Covers the query families the UI advertises (top-N, distributions,
    time-series, IP lookups, filters). Anything it cannot confidently map falls
    through to a safe sample query, which the guardrail and engine treat as a
    graceful degradation.
    """

    _IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
    _DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
    _TOPN_RE = re.compile(r"\btop\s+(\d+)\b")

    def generate(self, question: str, catalog: SchemaCatalog) -> str:
        q = question.lower()
        ip = self._IP_RE.search(question)
        date = self._DATE_RE.search(question)
        topn = self._TOPN_RE.search(q)
        n = int(topn.group(1)) if topn else 10

        # Longest-match first so 'Fraud_Ecommerce' beats 'Ecommerce'.
        known_types = sorted(catalog.distinct_values.get("data_type", []),
                             key=len, reverse=True)
        mentioned_type = next((t for t in known_types if t.lower() in q), None)

        # 1. Time-series / trend
        if any(k in q for k in ("hourly", "trend", "over time", "timeline", "per hour")):
            where = "WHERE CAST(timestamp AS DATE) = '%s'\n" % date.group(1) if date else ""
            return (
                "SELECT DATE_TRUNC('hour', timestamp) AS hour, COUNT(*) AS events\n"
                "FROM ipdr_logs\n"
                + where +
                "GROUP BY hour\nORDER BY hour\nLIMIT 100;"
            )
        if "daily" in q:
            return (
                "SELECT DATE_TRUNC('day', timestamp) AS day, COUNT(*) AS events\n"
                "FROM ipdr_logs\nGROUP BY day\nORDER BY day\nLIMIT 100;"
            )

        # 2. Distribution / breakdown
        if any(k in q for k in ("distribution", "breakdown", "by category", "threat categ")):
            return (
                "SELECT data_type, COUNT(*) AS events\n"
                "FROM ipdr_logs\nGROUP BY data_type\nORDER BY events DESC\nLIMIT 100;"
            )
        if "tcp" in q and "udp" in q:
            return (
                "SELECT protocol, COUNT(*) AS events\n"
                "FROM ipdr_logs\nGROUP BY protocol\nORDER BY events DESC\nLIMIT 100;"
            )

        # 3. First / last activity
        if "first" in q and ip:
            return (
                "SELECT timestamp, destination_domain, activity, data_type\n"
                "FROM ipdr_logs\n"
                "WHERE source_ip = '%s'\n" % ip.group(1) +
                "ORDER BY timestamp ASC\nLIMIT 1;"
            )
        if "last" in q and ip:
            return (
                "SELECT timestamp, destination_domain, activity, data_type\n"
                "FROM ipdr_logs\n"
                "WHERE source_ip = '%s'\n" % ip.group(1) +
                "ORDER BY timestamp DESC\nLIMIT 1;"
            )

        # 4. Top-N domains for an IP
        if ("top" in q or "most" in q) and ("domain" in q or "site" in q) and ip:
            return (
                "SELECT destination_domain, COUNT(*) AS hits\n"
                "FROM ipdr_logs\n"
                "WHERE source_ip = '%s'\n" % ip.group(1) +
                "GROUP BY destination_domain\nORDER BY hits DESC\nLIMIT %d;" % n
            )

        # 5. Top / most active source IPs
        if ("top" in q or "most" in q) and ("ip" in q or "user" in q or "actor" in q):
            return (
                "SELECT source_ip, COUNT(*) AS events\n"
                "FROM ipdr_logs\nGROUP BY source_ip\nORDER BY events DESC\n"
                "LIMIT %d;" % n
            )

        # 6. Filter by a known threat category
        if mentioned_type:
            return (
                "SELECT timestamp, source_ip, destination_domain, activity, data_type\n"
                "FROM ipdr_logs\n"
                "WHERE data_type = '%s'\n" % mentioned_type +
                "ORDER BY timestamp\nLIMIT 100;"
            )

        # 7. All activity for an IP
        if ip:
            return (
                "SELECT timestamp, destination_domain, activity, data_type\n"
                "FROM ipdr_logs\n"
                "WHERE source_ip = '%s'\n" % ip.group(1) +
                "ORDER BY timestamp\nLIMIT 100;"
            )

        # 8. Fallback - safe sample.
        return "SELECT * FROM ipdr_logs ORDER BY timestamp LIMIT 50;"

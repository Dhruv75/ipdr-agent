"""Result narration.

* :class:`LLMNarrator`      - GPT-4o forensic-analyst summary (cloud path).
* :class:`TemplateNarrator` - deterministic summary built from the result frame
                              (offline path). No made-up numbers - it only
                              reports what is actually in the data.
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd

from .llm.base import LLMProvider
from .router import RouteDecision


class Narrator(Protocol):
    def narrate(self, query: str, sql: str, data: pd.DataFrame,
                decision: RouteDecision) -> str: ...


def _preview(data: pd.DataFrame, max_rows: int = 50) -> str:
    if data.empty:
        return "No results found."
    if len(data) <= max_rows:
        return data.to_string(index=False)
    head = data.head(max_rows // 2).to_string(index=False)
    tail = data.tail(max_rows // 2).to_string(index=False)
    return f"{head}\n... [truncated] ...\n{tail}"


class LLMNarrator:
    _SYSTEM = (
        "You are a senior network forensic analyst. Given a question and query "
        "results, write a concise (2-5 sentence) analysis. Start with a direct "
        "answer, cite specific numbers/IPs/domains from the data, and flag any "
        "security concern. Never invent numbers not present in the results. Use "
        "**bold** for the key finding."
    )

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def narrate(self, query: str, sql: str, data: pd.DataFrame,
                decision: RouteDecision) -> str:
        user = (
            f"QUESTION: {query}\n\nSQL:\n{sql}\n\n"
            f"RESULTS ({len(data)} rows):\n{_preview(data)}"
        )
        try:
            return self.provider.chat(system=self._SYSTEM, user=user,
                                      temperature=0.3, max_tokens=500)
        except Exception as e:
            return TemplateNarrator().narrate(query, sql, data, decision) + \
                f"\n\n_(LLM narration unavailable: {e})_"


class TemplateNarrator:
    """Deterministic, data-grounded summary. Zero external calls."""

    def narrate(self, query: str, sql: str, data: pd.DataFrame,
                decision: RouteDecision) -> str:
        if data.empty:
            return "**No matching records** were found for this query."

        rows = len(data)
        cols = list(data.columns)
        lines = [f"Returned **{rows} row(s)** with columns: {', '.join(cols)}."]

        # If it looks like an aggregation (2 cols, second numeric), report the top item.
        if len(cols) >= 2 and pd.api.types.is_numeric_dtype(data[cols[1]]):
            top = data.iloc[0]
            lines.append(
                f"Top result: **{top[cols[0]]}** with {top[cols[1]]} "
                f"({cols[1]})."
            )
            total = data[cols[1]].sum()
            lines.append(f"Sum of {cols[1]} across shown rows: {total}.")

        # Surface any threat categories present.
        if "data_type" in data.columns:
            threats = [t for t in data["data_type"].unique()
                       if any(k in str(t) for k in
                              ("Fraud", "C2", "Exfil", "Beacon"))]
            if threats:
                lines.append(
                    "Potential threat categories present: "
                    f"**{', '.join(map(str, threats))}**."
                )
        return " ".join(lines)

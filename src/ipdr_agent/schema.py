"""Schema-context construction for the NL-to-SQL prompt.

Instead of hand-writing the schema into the system prompt (which drifts from
reality and causes hallucinated columns), we introspect the actual DataFrame
and build the catalog dynamically. This is "dynamic catalog injection".
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pandas.api import types as pdt

TABLE_NAME = "ipdr_logs"

# Short, curated descriptions per column. Anything not listed still appears in
# the catalog with its dtype - the description just improves grounding.
COLUMN_DOCS = {
    "timestamp": "Event occurrence time (TIMESTAMP).",
    "source_ip": "Internal user/actor IP address.",
    "destination_ip": "Target server IP address.",
    "destination_domain": "Destination website domain (e.g. youtube.com, hdfcbank.com).",
    "activity": "Specific action performed (e.g. 'Banking Login', 'Beacon Check-in').",
    "data_type": "Threat/behaviour classification (e.g. Banking, C2_Beaconing, Fraud_Ecommerce).",
    "port": "Network port (INTEGER, mostly 443).",
    "protocol": "Network protocol (TCP / UDP).",
    "rag_text": "Natural-language summary of the record (used for semantic search).",
}


@dataclass
class SchemaCatalog:
    """A compact, LLM-ready description of the queryable table."""

    columns: dict[str, str]           # name -> sql type
    row_count: int
    date_range: tuple[str, str]
    distinct_values: dict[str, list[str]]   # low-cardinality columns -> values

    def to_prompt(self) -> str:
        col_lines = []
        for name, sqltype in self.columns.items():
            doc = COLUMN_DOCS.get(name, "")
            col_lines.append(f"  - {name} ({sqltype}): {doc}".rstrip())
        cols = "\n".join(col_lines)

        enum_lines = []
        for col, values in self.distinct_values.items():
            preview = ", ".join(values[:20])
            enum_lines.append(f"  - {col}: {preview}")
        enums = "\n".join(enum_lines) if enum_lines else "  (none)"

        return (
            f"TABLE: {TABLE_NAME}\n"
            f"ROWS: {self.row_count:,}\n"
            f"DATE_RANGE: {self.date_range[0]} to {self.date_range[1]}\n\n"
            f"COLUMNS:\n{cols}\n\n"
            f"LOW-CARDINALITY VALUE CATALOG (use these exact literals):\n{enums}\n"
        )


_PANDAS_TO_SQL = {
    "datetime64[ns]": "TIMESTAMP",
    "int64": "INTEGER",
    "float64": "DOUBLE",
    "object": "VARCHAR",
    "string": "VARCHAR",
    "str": "VARCHAR",
    "bool": "BOOLEAN",
}


def build_catalog(df: pd.DataFrame, max_enum_cardinality: int = 40) -> SchemaCatalog:
    """Introspect ``df`` and produce a :class:`SchemaCatalog`.

    Low-cardinality string columns get their distinct values enumerated so the
    LLM uses real literals (e.g. it learns that ``data_type`` can be
    ``'C2_Beaconing'``) - this is the main defence against hallucinated filters.
    """
    columns = {c: _PANDAS_TO_SQL.get(str(df[c].dtype), "VARCHAR") for c in df.columns}

    if "timestamp" in df.columns:
        date_range = (str(df["timestamp"].min()), str(df["timestamp"].max()))
    else:
        date_range = ("n/a", "n/a")

    distinct_values: dict[str, list[str]] = {}
    for col in df.columns:
        if col == "rag_text":
            continue
        series = df[col]
        # Enumerate string-like columns whether pandas represents them as the
        # legacy "object" dtype or the newer "string"/"str" dtype (pandas 3.x
        # default). Using dtype == object alone silently drops them under new
        # pandas and empties the value catalog.
        if pdt.is_object_dtype(series) or pdt.is_string_dtype(series):
            nunique = series.nunique(dropna=True)
            if 0 < nunique <= max_enum_cardinality:
                distinct_values[col] = sorted(map(str, series.dropna().unique()))

    return SchemaCatalog(
        columns=columns,
        row_count=len(df),
        date_range=date_range,
        distinct_values=distinct_values,
    )

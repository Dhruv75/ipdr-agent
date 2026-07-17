"""SQL guardrails - the safety layer between the LLM and the database.

Executing raw LLM-generated SQL is dangerous: prompt injection or a confused
model can emit ``DROP TABLE``, ``ATTACH`` a remote database, read the local
filesystem via ``read_csv``, or exfiltrate data via ``COPY ... TO``. This module
validates and normalises SQL *before* it ever reaches DuckDB.

Strategy (defence in depth):
  1. Parse with sqlglot when available (real AST inspection). Fall back to a
     conservative regex screen if sqlglot is not installed.
  2. Allow exactly one statement.
  3. Allow only read operations: the root must be SELECT (or a WITH wrapping a
     SELECT). Everything else is rejected.
  4. Block a denylist of dangerous tokens/functions regardless of parse result.
  5. Ensure only allow-listed tables are referenced.
  6. Force a LIMIT (inject a default, clamp anything above the max).

The DuckDB connection is *also* hardened at the engine layer (external file
access disabled via ``SET enable_external_access=false``), so this is
belt-and-suspenders, not the only line of defence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

try:
    import sqlglot
    from sqlglot import exp

    _HAVE_SQLGLOT = True
except Exception:  # pragma: no cover
    _HAVE_SQLGLOT = False


class GuardrailError(ValueError):
    """Raised when a SQL statement violates a safety rule."""


# Keywords/functions that must never appear in a read-only analytics query.
# Matched on WORD BOUNDARIES against SQL whose string literals have been blanked
# out first, so legitimate data values that merely *contain* one of these
# substrings are not false-flagged: the domain 'megafileupload.io' -> "load",
# the activity 'WhatsApp Call' -> "call", or an OFFSET clause -> "set".
_DENY_WORDS = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "attach", "detach", "copy", "pragma", "install", "load", "export",
    "read_csv", "read_parquet", "read_json", "read_text", "glob",
    "system", "shell", "call", "set", "reset", "grant", "revoke",
)
_DENY_RE = re.compile(r"\b(" + "|".join(_DENY_WORDS) + r")\b", re.IGNORECASE)
# Single-quoted string literals (handling the '' escape) - blanked before screen.
_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

_SEMICOLON_SPLIT = re.compile(r";\s*\S")


@dataclass
class ValidatedSQL:
    sql: str
    limit: int
    tables: tuple[str, ...]


class SQLGuardrail:
    def __init__(self, allowed_tables: tuple[str, ...], default_limit: int,
                 max_limit: int):
        self.allowed_tables = {t.lower() for t in allowed_tables}
        self.default_limit = default_limit
        self.max_limit = max_limit

    # -- public API ----------------------------------------------------
    def validate(self, sql: str) -> ValidatedSQL:
        sql = self._strip(sql)
        if not sql:
            raise GuardrailError("Empty SQL statement.")

        self._deny_screen(sql)
        self._single_statement(sql)

        if _HAVE_SQLGLOT:
            return self._validate_ast(sql)
        return self._validate_regex(sql)

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _strip(sql: str) -> str:
        sql = sql.replace("```sql", "").replace("```", "").strip()
        # remove line comments and block comments
        sql = re.sub(r"--[^\n]*", "", sql)
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
        return sql.strip().rstrip(";").strip()

    def _deny_screen(self, sql: str) -> None:
        # Blank out string literals so values inside quotes can never trip the
        # denylist, then match dangerous keywords on word boundaries only.
        scrubbed = _STRING_LITERAL_RE.sub("''", sql)
        match = _DENY_RE.search(scrubbed)
        if match:
            raise GuardrailError(
                f"Disallowed keyword/function detected: '{match.group(1).lower()}'."
            )

    @staticmethod
    def _single_statement(sql: str) -> None:
        # after stripping the trailing ';', any remaining ';<something>' means
        # a second statement was stacked.
        if _SEMICOLON_SPLIT.search(sql):
            raise GuardrailError("Multiple SQL statements are not allowed.")

    # -- AST path (preferred) -----------------------------------------
    def _validate_ast(self, sql: str) -> ValidatedSQL:
        try:
            statements = sqlglot.parse(sql, read="duckdb")
        except Exception as e:  # parse failure -> reject rather than guess
            raise GuardrailError(f"Could not parse SQL: {e}") from e

        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            raise GuardrailError("Exactly one statement is required.")

        tree = statements[0]
        if not isinstance(tree, (exp.Select, exp.With, exp.Subquery)):
            raise GuardrailError(
                f"Only read-only SELECT queries are allowed (got {type(tree).__name__})."
            )

        # No DML/DDL nodes anywhere in the tree.
        for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
                    exp.Alter, exp.Command):
            if tree.find(bad):
                raise GuardrailError(f"Forbidden operation: {bad.__name__}.")

        # Table allow-list. CTE names are local aliases, not physical tables,
        # so exclude them before checking against the allow-list.
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        tables = tuple(sorted({t.name.lower() for t in tree.find_all(exp.Table)}
                              - cte_names))
        for t in tables:
            if t and t not in self.allowed_tables:
                raise GuardrailError(f"Access to table '{t}' is not permitted.")

        # Enforce LIMIT.
        select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
        limit = self._extract_and_clamp_limit(select)
        final_sql = tree.sql(dialect="duckdb") + ";"
        return ValidatedSQL(sql=final_sql, limit=limit, tables=tables)

    def _extract_and_clamp_limit(self, select) -> int:
        if select is None:
            return self.default_limit
        limit_node = select.args.get("limit")
        if limit_node is None:
            select.limit(self.default_limit, copy=False)
            return self.default_limit
        try:
            current = int(limit_node.expression.this)
        except Exception:
            select.limit(self.default_limit, copy=False)
            return self.default_limit
        if current > self.max_limit:
            select.limit(self.max_limit, copy=False)
            return self.max_limit
        return current

    # -- regex fallback ------------------------------------------------
    def _validate_regex(self, sql: str) -> ValidatedSQL:
        """Weaker, dependency-free validation used only when sqlglot is absent.

        Keeps the same guarantees in spirit as the AST path (read-only,
        allow-listed tables, forced LIMIT) but enforced with regex. It ALWAYS
        returns a :class:`ValidatedSQL`; it must never fall through to ``None``.
        """
        low = sql.lower().lstrip("(")
        if not (low.startswith("select") or low.startswith("with")):
            raise GuardrailError("Only SELECT/WITH queries are allowed.")

        # Exclude CTE aliases so they are not mistaken for physical tables that
        # would need to be on the allow-list.
        cte_names = set(re.findall(r"(?:with|,)\s+([a-zA-Z_]\w*)\s+as\s*\(", low))
        referenced = (re.findall(r"from\s+([a-zA-Z_][\w]*)", low)
                      + re.findall(r"join\s+([a-zA-Z_][\w]*)", low))
        tables = tuple(sorted(set(referenced) - cte_names))
        for t in tables:
            if t and t not in self.allowed_tables:
                raise GuardrailError(f"Access to table '{t}' is not permitted.")

        # Force a LIMIT: clamp if present-and-too-large, inject if absent.
        m = re.search(r"limit\s+(\d+)", low)
        if m:
            current = int(m.group(1))
            if current > self.max_limit:
                sql = re.sub(r"(?i)limit\s+\d+", f"LIMIT {self.max_limit}", sql)
                limit = self.max_limit
            else:
                limit = current
        else:
            sql = f"{sql}\nLIMIT {self.default_limit}"
            limit = self.default_limit

        return ValidatedSQL(sql=sql.strip().rstrip(";") + ";", limit=limit,
                            tables=tables)

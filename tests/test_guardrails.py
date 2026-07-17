"""Security tests for the SQL guardrail - the most interview-relevant module."""
import pytest

from ipdr_agent.sql.guardrails import GuardrailError, SQLGuardrail

GUARD = SQLGuardrail(allowed_tables=("ipdr_logs",), default_limit=100, max_limit=1000)


# --- things that must be REJECTED -----------------------------------
@pytest.mark.parametrize("evil", [
    "DROP TABLE ipdr_logs",
    "DELETE FROM ipdr_logs",
    "UPDATE ipdr_logs SET port = 0",
    "INSERT INTO ipdr_logs VALUES (1)",
    "SELECT * FROM ipdr_logs; DROP TABLE ipdr_logs",           # stacked statement
    "SELECT * FROM ipdr_logs; DELETE FROM ipdr_logs",
    "ATTACH 'evil.db' AS e",
    "COPY ipdr_logs TO '/tmp/leak.csv'",
    "SELECT * FROM read_csv('/etc/passwd')",                    # file read
    "PRAGMA database_list",
    "SELECT * FROM secret_table",                               # non-allowlisted table
])
def test_rejects_dangerous_sql(evil):
    with pytest.raises(GuardrailError):
        GUARD.validate(evil)


# --- things that must be ACCEPTED -----------------------------------
def test_allows_plain_select_and_injects_limit():
    v = GUARD.validate("SELECT data_type, COUNT(*) FROM ipdr_logs GROUP BY data_type")
    assert v.limit == 100
    assert "limit" in v.sql.lower()
    assert v.tables == ("ipdr_logs",)


def test_allows_cte():
    sql = (
        "WITH t AS (SELECT source_ip, COUNT(*) c FROM ipdr_logs GROUP BY source_ip) "
        "SELECT * FROM t ORDER BY c DESC LIMIT 5"
    )
    v = GUARD.validate(sql)
    assert v.limit == 5


def test_clamps_excessive_limit():
    v = GUARD.validate("SELECT * FROM ipdr_logs LIMIT 999999")
    assert v.limit == 1000


def test_strips_markdown_fences_and_comments():
    sql = "```sql\n-- top domains\nSELECT destination_domain FROM ipdr_logs\n```"
    v = GUARD.validate(sql)
    assert v.sql.lower().startswith("select")


# --- regression: legitimate literals that merely CONTAIN a deny substring ----
# (The old substring denylist wrongly rejected these. 'megafileupload.io' is the
# app's own planted data-exfil domain, so filtering on it MUST work.)
@pytest.mark.parametrize("good", [
    "SELECT * FROM ipdr_logs WHERE destination_domain = 'megafileupload.io'",  # load
    "SELECT * FROM ipdr_logs WHERE activity = 'WhatsApp Call'",                # call
    "SELECT * FROM ipdr_logs WHERE activity = 'Large File Upload'",            # load
    "SELECT * FROM ipdr_logs ORDER BY timestamp LIMIT 10 OFFSET 20",           # set
])
def test_accepts_literals_containing_deny_substrings(good):
    v = GUARD.validate(good)
    assert v.sql.lower().startswith("select")


# --- regression: the regex fallback used when sqlglot is unavailable ---------
def test_regex_fallback_returns_valid_sql(monkeypatch):
    """With sqlglot absent, validate() must still return a ValidatedSQL (the old
    code fell through and returned None)."""
    from ipdr_agent.sql import guardrails as g
    monkeypatch.setattr(g, "_HAVE_SQLGLOT", False)
    v = GUARD.validate("SELECT data_type, COUNT(*) FROM ipdr_logs GROUP BY data_type")
    assert v is not None
    assert v.limit == 100                     # LIMIT injected when absent
    assert "limit" in v.sql.lower()


def test_regex_fallback_still_blocks_writes(monkeypatch):
    from ipdr_agent.sql import guardrails as g
    monkeypatch.setattr(g, "_HAVE_SQLGLOT", False)
    with pytest.raises(GuardrailError):
        GUARD.validate("DROP TABLE ipdr_logs")
    with pytest.raises(GuardrailError):
        GUARD.validate("SELECT * FROM secret_table")

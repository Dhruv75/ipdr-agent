"""Dual-engine evaluation harness.

Measures the two engines separately, because "accuracy" means different things
for each:

  SQL engine  -> Execution Success Rate  (did a guardrail-valid query run and
                 return rows?) + Expectation Pass Rate (right columns/values).
  RAG engine  -> Retrieval Relevance      (Recall@k: does the top-k contain a
                 record of the expected threat category?).

This is deliberately framework-light so it runs in CI with no paid keys. The
same golden set can be piped into Ragas/TruLens for LLM-graded faithfulness once
an API key is present - see docs/ARCHITECTURE.md.

Usage:
    PYTHONPATH=src python eval/run_eval.py
Exit code is non-zero if any metric falls below its threshold (CI gate).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ipdr_agent import Mode, Settings  # noqa: E402
from ipdr_agent.engine import load_engine  # noqa: E402

# Thresholds the build must meet.
SQL_SUCCESS_THRESHOLD = 1.0        # every SQL case must execute
SQL_EXPECT_THRESHOLD = 0.85        # >=85% of expectation checks pass
RAG_RECALL_THRESHOLD = 0.60        # offline hashing floor; real embeddings score higher


def _load_cases() -> dict:
    with open(ROOT / "eval" / "golden_queries.yaml") as f:
        return yaml.safe_load(f)


def evaluate_sql(engine, cases) -> tuple[float, float, list[str]]:
    executed = 0
    expectations_total = 0
    expectations_passed = 0
    log = []
    for case in cases:
        res = engine.answer(case["query"])
        ok_exec = res.success and res.rows >= case.get("min_rows", 1)
        executed += int(ok_exec)

        route_ok = (res.decision and res.decision.strategy == case.get("expect_route", "sql"))
        status = "PASS" if ok_exec else "FAIL"

        # Expectation checks
        detail = []
        if "expect_cols" in case:
            for col in case["expect_cols"]:
                expectations_total += 1
                present = col in res.data.columns
                expectations_passed += int(present)
                detail.append(f"col:{col}={'ok' if present else 'MISSING'}")
        if "expect_value" in case:
            col = case.get("expect_value_col", list(case["expect_value"])[0])
            want = case["expect_value"][col]
            expectations_total += 1
            got = (col in res.data.columns and
                   (res.data[col].astype(str) == str(want)).all())
            expectations_passed += int(got)
            detail.append(f"val:{col}=={want} -> {'ok' if got else 'BAD'}")

        log.append(
            f"  [{status}] {case['id']:<22} rows={res.rows:<4} "
            f"route={res.decision.strategy if res.decision else '?'}"
            f"{'' if route_ok else ' (route!)'}  {' '.join(detail)}"
        )
    exec_rate = executed / len(cases) if cases else 1.0
    expect_rate = (expectations_passed / expectations_total) if expectations_total else 1.0
    return exec_rate, expect_rate, log


def evaluate_rag(engine, cases, k: int = 30) -> tuple[float, list[str]]:
    """Retrieval Recall@k measured directly on the semantic engine.

    We call semantic_search (not answer) so we are grading retrieval quality,
    not the router's strategy choice.
    """
    hits = 0
    log = []
    for case in cases:
        data = engine.semantic_search(case["query"], limit=k)
        want = case["relevant_type"]
        found = ("data_type" in data.columns and
                 want in set(data["data_type"].astype(str)))
        hits += int(found)
        log.append(f"  [{'HIT' if found else 'miss'}] {case['id']:<20} "
                   f"target={want:<18} retrieved={len(data)}")
    recall = hits / len(cases) if cases else 1.0
    return recall, log


def main() -> int:
    cases = _load_cases()
    # Force local mode so the eval is deterministic and key-free in CI.
    engine = load_engine(Settings(mode=Mode.LOCAL))

    print(f"Engine mode: {engine.mode_label}\n")

    print("== SQL ENGINE ==")
    exec_rate, expect_rate, sql_log = evaluate_sql(engine, cases["sql_cases"])
    print("\n".join(sql_log))
    print(f"  -> Execution success rate : {exec_rate:.0%}")
    print(f"  -> Expectation pass rate  : {expect_rate:.0%}\n")

    print("== RAG ENGINE ==")
    recall, rag_log = evaluate_rag(engine, cases["rag_cases"])
    print("\n".join(rag_log))
    print(f"  -> Retrieval Recall@30    : {recall:.0%}\n")

    passed = (
        exec_rate >= SQL_SUCCESS_THRESHOLD
        and expect_rate >= SQL_EXPECT_THRESHOLD
        and recall >= RAG_RECALL_THRESHOLD
    )
    print("RESULT:", "PASS ✅" if passed else "FAIL ❌")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

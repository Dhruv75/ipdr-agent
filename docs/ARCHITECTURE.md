# Architecture & Engineering Notes

This document explains the design decisions behind IPDR Forensic Agent v5.0 and
maps them to the four areas that matter for a production-grade, interview-ready
system: **optimization, resilience/evaluation, security, and portability.**

---

## 1. Architectural & code optimizations

### 1.1 NL-to-SQL robustness

The single biggest source of failure in a naive Text-to-SQL app is **hallucinated
schema** — the model invents a column or a filter value that does not exist. Three
techniques mitigate this here:

- **Dynamic catalog injection** (`schema.py`). Instead of a hand-written schema
  string that drifts from reality, the catalog is introspected from the live
  dataframe at startup: column names, inferred SQL types, the row count, the date
  range, and — crucially — the **enumerated distinct values of every
  low-cardinality column**. The model is told verbatim that `data_type` can be
  `'C2_Beaconing'`, `'Fraud_Ecommerce'`, etc., so it stops guessing literals.
- **Few-shot prompting** (`sql/examples.py`). Eight curated `(question, SQL)`
  pairs pin the model to this schema and the DuckDB dialect (`DATE_TRUNC`,
  `CAST(timestamp AS DATE)`). The same pairs double as golden eval cases.
- **Schema pruning by cardinality.** Only columns with ≤ 40 distinct values are
  enumerated; high-cardinality fields (IPs, `rag_text`) are described but not
  dumped, keeping the prompt small and focused.

Future work: retrieve the *k* most similar few-shot examples per query (dynamic
few-shot) rather than sending all of them.

### 1.2 Hybrid routing / orchestration

Routing is **deterministic-first** (`router.py`):

1. A rule-based scorer counts keyword signals for each strategy/type and returns
   a decision with a confidence score. This is O(1), reproducible, and unit-tested.
2. Only when confidence is below a threshold *and* an LLM provider is configured
   does it fall back to a `gpt-4o-mini` JSON classifier.

Why: routing every query through an LLM adds latency and cost and is hard to test.
A rules engine handles the ~95% of forensic queries that are structurally obvious
("top", "distribution", "hourly") and reserves the model for genuine ambiguity.

**Multi-step planning** is represented by the `hybrid` strategy: a query such as
*"find the top talkers, then show semantically similar payloads"* runs the SQL
step, and on failure/continuation hands off to the semantic engine. The engine's
`answer()` method is the seam where a fuller planner (e.g. a small state machine
or an agent loop) would slot in.

### 1.3 Embeddings & vector search

- The default embedder is `all-MiniLM-L6-v2` (384-d), run **locally** — no paid
  API for embeddings.
- The `VectorStore` interface has two implementations: an **in-memory** brute-force
  cosine store (fine for a few thousand rows) and a **Qdrant** store that supports
  **server-side payload filtering** (pre-filter to a `source_ip` or `data_type`
  before the ANN search). The latter is the reason to prefer a real vector DB at
  scale.
- A dependency-free **hashing embedder** (words + char trigrams) is the last-resort
  fallback so the pipeline is always runnable in CI.

Future work: **hybrid sparse+dense retrieval** (BM25 over `rag_text` fused with
cosine via reciprocal-rank fusion) would improve recall on rare-term queries like
"exfiltration"; and payload filtering can be wired to the router's extracted
entities for a true metadata-filtered RAG.

---

## 2. Resilience, evaluation & observability

### 2.1 Dual-engine evaluation

`eval/run_eval.py` measures the two engines with the metrics that actually apply
to each, because "accuracy" means different things:

| Engine | Metric | Definition |
|---|---|---|
| SQL | Execution success rate | Did a guardrail-valid query run and return ≥ *min_rows*? |
| SQL | Expectation pass rate | Right columns / right filter values? |
| RAG | Retrieval Recall@k | Does top-*k* contain a record of the target threat category? |

RAG is evaluated via `engine.semantic_search()` directly, so retrieval quality is
graded **independently of routing**. The harness returns a non-zero exit code when
any metric drops below its threshold, which is what gates CI.

This is deliberately framework-light so it runs offline. The same golden set can be
fed to **Ragas** (context precision/recall, faithfulness) or **TruLens** (the "RAG
triad": groundedness, context relevance, answer relevance) once an API key is
present — those add *LLM-graded* faithfulness on top of the deterministic checks
here.

### 2.2 Failure handling

Every stage degrades instead of crashing:

- LLM SQL fails guardrail/execution → deterministic heuristic generator retries.
- RAG unavailable → the query is answered via SQL with a warning.
- Qdrant unreachable → in-memory store.
- Embedding model missing → hashing embedder.

### 2.3 Observability (next steps)

The engine already returns a structured `QueryResult` (strategy, SQL, row count,
warnings). Production would add: structured logging with a per-query trace id,
latency/token counters around each LLM call, and OpenTelemetry spans for
route → generate → validate → execute → narrate.

---

## 3. SQL guardrails & security

Executing raw LLM SQL is the highest-risk part of the system. Defence in depth:

1. **AST validation** (`sqlglot`). The statement is parsed; the root must be
   `SELECT`/`WITH`. Any `Insert/Update/Delete/Drop/Create/Alter/Command` node
   anywhere in the tree is rejected.
2. **Denylist screen.** Tokens like `attach`, `copy`, `pragma`, `read_csv`,
   `install`, `load` are blocked regardless of parse result — these are the DuckDB
   vectors for filesystem reads and data exfiltration.
3. **Single-statement enforcement.** Stacked queries (`...; DROP TABLE ...`) are
   rejected.
4. **Table allow-list.** Only `ipdr_logs` may be referenced; CTE aliases are
   excluded from the check so legitimate `WITH` queries pass.
5. **Forced `LIMIT`.** A default limit is injected and any excessive limit is
   clamped, preventing accidental full-table dumps.
6. **Read-only connection.** DuckDB is opened over an in-memory view; even if a
   write slipped through, there is nothing persistent to damage.

A regex fallback provides a conservative subset of these checks if `sqlglot` is
unavailable. See `tests/test_guardrails.py` for the adversarial test matrix.

---

## 4. Portability & deployment

The original notebook used `%%writefile` + `pyngrok` and shipped secrets inline.
This rewrite is a conventional, deployable repository:

- **Decoupled layers.** All logic lives in the `ipdr_agent` package with no
  Streamlit import; the app is a thin view. The engine can equally drive a FastAPI
  service, the eval harness, or tests.
- **Config, not constants.** Everything is read once in `config.py` from env vars /
  `.env` / Streamlit secrets. No key is ever in source.
- **Dockerized.** A multi-stage-friendly `Dockerfile` builds the image and
  generates the dataset at build time; `docker-compose.yml` brings up the app
  alongside a real Qdrant.
- **CI.** GitHub Actions installs the core (offline) dependencies, lints, generates
  a dataset, runs pytest, and runs the evaluation gate on every push/PR.

### Suggested next step: split frontend and backend

Extract the engine behind a small FastAPI service (`POST /query → QueryResult`)
and have Streamlit call it over HTTP. That lets the compute-heavy backend scale
independently (and be reused by a CLI or notebook), while the UI stays stateless.

# Placement Interview Defense

A cheat-sheet for defending this architecture in a system-design interview.
Each section is a question you *will* be asked, followed by the answer to give
and the trade-off to name explicitly.

---

## The three hardest questions

### Q1. "You let an LLM generate SQL that you then execute. How do you stop it from dropping your tables or reading `/etc/passwd`?"

This is the security question, and it is the one most likely to sink a candidate.

**Answer.** I never trust generated SQL. It passes a guardrail layer before it
reaches the database:

1. It is parsed to an AST with `sqlglot`; the root node must be `SELECT` or
   `WITH`, and any DML/DDL node anywhere in the tree is rejected.
2. A denylist blocks DuckDB's dangerous surface — `attach`, `copy`, `pragma`,
   `read_csv`, `install`, `load` — which are the actual filesystem/exfil vectors.
3. Only a single statement is allowed, so `...; DROP TABLE` is caught.
4. Table references are checked against an allow-list (CTE aliases excluded).
5. A `LIMIT` is injected/clamped.
6. The denylist matches on **word boundaries after blanking string literals**,
   so a value like the domain `'megafileupload.io'` or the activity
   `'WhatsApp Call'` is never mistaken for the `load`/`call` keyword — a
   security control that rejects legitimate queries is its own failure mode.
7. Finally, the DuckDB connection itself is hardened with
   `SET enable_external_access=false`, so `ATTACH`, `COPY`, and `read_csv` are
   dead at the engine level too — even a guardrail bypass cannot reach the
   filesystem or network. And it is an in-memory view, so there is nothing
   persistent to damage.

**Trade-off to name.** AST validation over regex: regex is cheaper but brittle and
easy to bypass with comments/whitespace; the AST is authoritative but adds a parse
dependency. I keep a (fully-functional) regex fallback so the system still runs if
`sqlglot` is missing, accepting a weaker guarantee in that mode.

---

### Q2. "Why two engines? How do you decide SQL vs RAG, and doesn't routing with an LLM add latency and cost to every single query?"

This probes whether you understand your own orchestration.

**Answer.** SQL and semantic search answer different questions. Aggregations,
filters, top-N, and time-series are *exact* structured queries — SQL is correct
and cheap. "Find behaviour similar to beaconing" is a *fuzzy* similarity question
that has no clean `WHERE` clause — that is what embeddings are for. Routing is
**deterministic-first**: a rule-based scorer classifies ~95% of queries in
microseconds with a confidence score, and the `gpt-4o-mini` classifier is only
called as a tie-breaker when confidence is low. So most queries pay **zero** LLM
routing latency.

**Trade-off to name.** Latency/cost vs accuracy. Using `gpt-4o-mini` for routing
would be more flexible on weird phrasings but adds ~300–800 ms and a token cost to
every query and is non-deterministic (hard to test). The rules engine is instant
and unit-tested but needs maintenance as query patterns grow. I chose rules-first
and treat the LLM as a fallback, which is the cost-optimal point for a workload
that is mostly structured.

---

### Q3. "How do you know it works? How would you measure quality and catch regressions for a system with two very different engines?"

This is the evaluation/observability question.

**Answer.** I evaluate the engines separately because "accuracy" differs. For SQL I
measure **execution success rate** (did a guardrail-valid query run and return the
expected shape?) and an **expectation pass rate** (right columns, right filter
values) against a golden set. For RAG I measure **retrieval Recall@k** — does the
top-*k* contain a record of the target threat category? — calling the retrieval
engine directly so I'm grading retrieval, not routing. The harness fails the build
if any metric drops below threshold, so regressions are caught in CI. For deeper,
LLM-graded faithfulness I'd layer in Ragas (context precision/recall) or TruLens
(the RAG triad: groundedness, context relevance, answer relevance).

**Trade-off to name.** Deterministic checks are fast, free, and CI-friendly but
only test structure/retrieval, not whether the narrative is faithful. LLM-graded
metrics test faithfulness but cost money, add variance, and need a key — so I gate
CI on the deterministic checks and run the LLM-graded suite periodically.

---

## Rapid-fire trade-off answers

**Why DuckDB instead of just querying the pandas DataFrame?**
DuckDB gives me a real SQL surface (the whole point of Text-to-SQL), vectorized
columnar execution that beats pandas on group-bys/joins, and — importantly — a
security boundary I can open **read-only**. Doing NL-to-pandas would mean
generating and `exec`-ing Python, which is far harder to sandbox than validating a
SQL AST. Cost: another dependency and a data-registration step.

**Why local `all-MiniLM-L6-v2` instead of OpenAI embeddings?**
It's free, private (log data never leaves the box), and fast enough at this scale.
OpenAI embeddings are stronger but add cost, latency, and a data-egress concern for
forensic logs. The interface lets me swap either way.

**Why Qdrant instead of FAISS or pgvector?**
Qdrant gives managed ANN plus **server-side payload filtering**, so I can restrict
to a `source_ip`/`data_type` before the vector search — metadata-filtered RAG.
FAISS is a library, not a service (no filtering/persistence out of the box);
pgvector is great if you're already on Postgres. For a standalone forensic tool,
Qdrant's filtering was the deciding feature. I still ship an in-memory store so
nothing *requires* Qdrant.

**Why `gpt-4o` for SQL but `gpt-4o-mini` for routing?**
SQL generation is correctness-critical and benefits from the stronger model;
routing is a simple classification where `mini` is accurate enough and 10–20×
cheaper. Matching model strength to task difficulty is the cost lever.

**What breaks at 10M rows instead of 5k?**
The in-memory vector store and loading the whole file into memory. Fixes: move
embeddings to Qdrant (already supported), stream/partition the DuckDB source
(Parquet + predicate pushdown), and cache the schema catalog. The SQL path scales
well because DuckDB is built for analytical scans; the RAG path is what needs the
managed store.

**How would you handle prompt injection via the data itself (e.g. a domain named
`ignore-previous-instructions.com`)?**
The data only ever reaches the LLM as *results to summarize*, never as
instructions, and the narrator is told to only report values present in the data.
The SQL guardrail means even a malicious value can't escalate into a write. I'd add
output validation on the narrative for defense in depth.

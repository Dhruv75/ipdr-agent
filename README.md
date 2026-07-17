# 🔒 IPDR Forensic Agent v5.0

A hybrid **Text-to-SQL + semantic RAG** agent for investigating IPDR
(IP Detail Record) network-log forensics in natural language. Ask questions
like *"top domains for 10.22.45.61"* or *"find beaconing-like behaviour"* and
get a guardrailed SQL answer, an auto-generated chart, and a plain-English
forensic summary.

Built to be **runnable with zero paid API keys** (deterministic local mode) and
to scale up to GPT-4o + Qdrant when credentials are provided.

![CI](https://github.com/USERNAME/ipdr-forensic-agent/actions/workflows/ci.yml/badge.svg)

---

## Why this project is interesting

- **Guardrailed SQL execution.** LLM-generated SQL is parsed to an AST
  (`sqlglot`), screened against a denylist, restricted to read-only `SELECT`
  over an allow-listed table, and force-`LIMIT`-ed *before* it touches a
  read-only DuckDB connection. See [`sql/guardrails.py`](src/ipdr_agent/sql/guardrails.py).
- **Deterministic-first routing.** A rule-based scorer decides
  `sql | rag | hybrid` with zero latency; the LLM classifier is only a
  tie-breaker for low-confidence queries.
- **Dynamic catalog injection + few-shot.** The NL-to-SQL prompt is built from
  the *actual* dataframe schema (columns, dtypes, low-cardinality value
  enumerations), which is the main defence against hallucinated columns/filters.
- **Graceful degradation everywhere.** OpenAI → local heuristic generator;
  Qdrant → in-memory cosine; sentence-transformers → hashing embedder. Nothing
  is a hard dependency at runtime.
- **A real evaluation harness.** SQL execution success + expectation checks and
  RAG retrieval Recall@k are measured *separately* and gate CI.

---

## Architecture

```
                         ┌─────────────────────────┐
   natural-language ───▶ │      QueryRouter        │  deterministic rules
   question              │  (rules → LLM fallback) │  + optional LLM tie-break
                         └───────────┬─────────────┘
                       strategy = sql│rag│hybrid
              ┌────────────────────────┴───────────────────────┐
              ▼                                                 ▼
   ┌─────────────────────┐                          ┌────────────────────────┐
   │  NL-to-SQL Generator│  few-shot + dynamic      │  Semantic Retrieval    │
   │  LLM  or  Heuristic │  catalog injection       │  Embedder + VectorStore│
   └──────────┬──────────┘                          │  Qdrant or in-memory   │
              ▼                                      └───────────┬────────────┘
   ┌─────────────────────┐                                      │
   │   SQL Guardrail     │  sqlglot AST · denylist ·            │
   │  (validate + clamp) │  table allow-list · force LIMIT      │
   └──────────┬──────────┘                                      │
              ▼                                                  │
   ┌─────────────────────┐                                      │
   │  DuckDB (read-only) │◀── in-memory view over the dataframe │
   └──────────┬──────────┘                                      │
              └───────────────┬──────────────────────────────────┘
                              ▼
                  ┌───────────────────────┐
                  │ Narrator + Auto-Viz   │ LLM or template summary + Plotly
                  └───────────────────────┘
```

Full write-up: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ·
Interview defense: [`docs/INTERVIEW.md`](docs/INTERVIEW.md)

---

## Quickstart

### Local, no API keys (fully offline)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_data.py --rows 5000        # synthetic dataset
streamlit run app/streamlit_app.py                 # open http://localhost:8501
```

The app boots in **LOCAL** mode: deterministic NL-to-SQL, in-memory vector
search, and template narration. No OpenAI or Qdrant needed.

### With OpenAI + Qdrant (cloud path)

```bash
pip install -r requirements-cloud.txt   # core + openai + qdrant + embeddings
cp .env.example .env        # fill in OPENAI_API_KEY, QDRANT_URL, QDRANT_API_KEY
python scripts/index_qdrant.py   # optional: index into managed Qdrant
streamlit run app/streamlit_app.py
```

### Docker

```bash
docker compose up --build   # app on :8501, Qdrant on :6333
```

### Deploy a public demo (free)

The app runs key-free, so it deploys as a public link with **zero API cost**.
See [`DEPLOY.md`](DEPLOY.md) for step-by-step **Streamlit Community Cloud**
instructions (the dataset is auto-generated on first run, so there is nothing
to upload).

---

## Repository layout

```
ipdr-forensic-agent/
├── src/ipdr_agent/          # the engine (no Streamlit dependency)
│   ├── config.py            # env-driven settings, mode selection
│   ├── schema.py            # dynamic catalog construction
│   ├── router.py            # deterministic-first query router
│   ├── engine.py            # orchestrator
│   ├── narrative.py         # LLM + template narrators
│   ├── viz.py               # Plotly auto-visualization
│   ├── embeddings.py        # sentence-transformers + hashing fallback
│   ├── llm/                 # provider abstraction (OpenAI)
│   ├── sql/                 # generator, guardrails, few-shot examples
│   └── vector/              # VectorStore: Qdrant + in-memory
├── app/streamlit_app.py     # thin UI layer
├── scripts/generate_data.py # synthetic IPDR generator (seeded)
├── eval/                    # golden set + dual-engine harness
├── tests/                   # pytest (guardrails, router, engine)
├── Dockerfile, docker-compose.yml, Makefile
└── .github/workflows/ci.yml
```

---

## Development

```bash
make install    # runtime + dev deps
make data       # generate dataset
make test       # pytest
make eval       # dual-engine evaluation gate
make lint       # ruff
```

---

## The synthetic dataset

`scripts/generate_data.py` produces ~5,000 seeded IPDR rows with a realistic
benign-browsing long tail plus three **planted, investigable threat scenarios**:

| Scenario | Actor IP | What to look for |
|---|---|---|
| E-commerce fraud | `10.22.45.61` | banking login → `shopfast-deals.net` → card submission |
| C2 beaconing | `10.22.45.77` | ~30-min fixed-interval calls to `cdn-analytics-sync.xyz` |
| Data exfiltration | `10.22.45.88` | sustained 2am upload burst to `megafileupload.io` |

The data is 100% synthetic. Regenerate any time with `make data`.

> **Note:** the original project shipped real credentials inline. This rewrite
> removes all secrets; configuration is via `.env` / Streamlit secrets only.

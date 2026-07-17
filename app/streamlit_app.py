"""Streamlit frontend for the IPDR Forensic Agent.

This is a THIN presentation layer. All analysis logic lives in the
``ipdr_agent`` package (engine, router, guardrails, generators). The app only:
  * loads settings + engine (cached),
  * renders the chat UI, charts, tables and SQL,
  * copies Streamlit secrets into the environment so ``config`` can read them.

Run:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Make the src/ package importable when run via `streamlit run`.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Bridge st.secrets -> environment so ipdr_agent.config can read them uniformly.
for key in ("OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY", "IPDR_MODE"):
    try:
        if key in st.secrets and st.secrets[key]:
            os.environ.setdefault(key, str(st.secrets[key]))
    except Exception:
        pass

from ipdr_agent import load_settings  # noqa: E402
from ipdr_agent.engine import ForensicEngine  # noqa: E402

st.set_page_config(page_title="IPDR Forensic Agent v5.0", layout="wide", page_icon="🔒")


def _ensure_dataset(path: Path) -> None:
    """Generate the synthetic dataset on first run if it is missing.

    The data file is gitignored, so on a host with no build step (Streamlit
    Community Cloud, Hugging Face Spaces) it will not exist on first launch.
    Rather than error, we create it here by reusing the *canonical* generator in
    scripts/generate_data.py - no duplicated logic, works everywhere.
    """
    if path.exists():
        return
    sys.path.insert(0, str(ROOT / "scripts"))
    from generate_data import build_dataframe  # reuse the one true generator

    df = build_dataframe(5000)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False)


@st.cache_resource(show_spinner="Preparing data + loading forensic engine...")
def get_engine() -> ForensicEngine | None:
    settings = load_settings()
    path = Path(settings.data_path)
    _ensure_dataset(path)
    if not path.exists():
        return None
    df = pd.read_excel(path) if str(path).endswith((".xlsx", ".xls")) else pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return ForensicEngine(df, settings)


engine = get_engine()
settings = load_settings()

# --- Header ----------------------------------------------------------
st.title("🔒 IPDR Forensic Agent v5.0")
st.caption("Deterministic routing · Guardrailed Text-to-SQL · Semantic RAG · Offline-capable")

# --- Sidebar ---------------------------------------------------------
with st.sidebar:
    st.header("⚙️ System Status")
    if engine is None:
        st.error("No data file found. Run `python scripts/generate_data.py` first.")
    else:
        st.success(f"Engine ready · **{engine.mode_label.upper()}** mode")
        st.metric("Records", f"{len(engine.df):,}")
        st.caption(
            f"Date range: {engine.df['timestamp'].min().date()} → "
            f"{engine.df['timestamp'].max().date()}"
        )

    st.divider()
    st.subheader("Backends")
    st.write("OpenAI:", "✅ configured" if settings.has_openai else "⛔ local fallback")
    st.write("Qdrant:", "✅ configured" if settings.has_qdrant else "⛔ in-memory")

    st.divider()
    st.subheader("Display")
    show_sql = st.checkbox("Show SQL", value=True)
    show_routing = st.checkbox("Show routing decision", value=True)
    max_rows = st.slider("Max rows in table", 10, 500, 100, 10)

    st.divider()
    st.subheader("💡 Try these")
    examples = [
        "Top 10 domains accessed by IP 10.22.45.61",
        "Distribution of threat categories",
        "Show the hourly activity trend on 2025-09-17",
        "List all Fraud_Ecommerce events",
        "Which source IPs generated the most events?",
        "First activity of IP 10.22.45.77",
        "Find beaconing-like behaviour",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True, key=f"ex_{hash(ex)}"):
            st.session_state.pending = ex


# --- Chat state ------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending" not in st.session_state:
    st.session_state.pending = None


def render_result(result, idx: int) -> None:
    if result.get("warning"):
        st.warning(result["warning"])
    st.markdown(result["narrative"])
    if result.get("chart") is not None:
        st.plotly_chart(result["chart"], use_container_width=True, key=f"chart_{idx}")
    if result.get("rows", 0) > 0:
        with st.expander(f"📊 Data ({result['rows']} rows)"):
            st.dataframe(result["data"].head(max_rows), use_container_width=True)
    if show_sql and result.get("sql"):
        with st.expander("🔧 SQL"):
            st.code(result["sql"], language="sql")
    if show_routing and result.get("routing"):
        r = result["routing"]
        with st.expander("🧭 Routing decision"):
            c1, c2, c3, c4 = st.columns(4)
            c1.caption(f"**Strategy**\n\n{r['strategy']}")
            c2.caption(f"**Type**\n\n{r['query_type']}")
            c3.caption(f"**Viz**\n\n{r['visualization']}")
            c4.caption(f"**Confidence**\n\n{r['confidence']:.2f} ({r['source']})")
            st.caption(f"Reasoning: {r['reasoning']}")


for i, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("result"):
            render_result(msg["result"], i)

# --- Input -----------------------------------------------------------
prompt = st.session_state.pending or st.chat_input("🔍 Ask a forensic question...")
st.session_state.pending = None

if prompt:
    if engine is None:
        st.error("Engine not initialised - generate the dataset first.")
        st.stop()

    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        with st.spinner("Analysing..."):
            res = engine.answer(prompt)
        if not res.success:
            st.error(f"Query failed: {res.error}")
            st.session_state.messages.append(
                {"role": "assistant", "content": f"**Error:** {res.error}"}
            )
        else:
            payload = {
                "narrative": res.narrative,
                "sql": res.sql,
                "data": res.data,
                "rows": res.rows,
                "chart": res.chart,
                "warning": res.warning,
                "routing": res.decision.as_dict() if res.decision else None,
            }
            render_result(payload, len(st.session_state.messages))
            st.session_state.messages.append(
                {"role": "assistant", "content": res.narrative, "result": payload}
            )

st.divider()
st.caption("IPDR Forensic Agent v5.0 · DuckDB · Plotly · Qdrant · OpenAI (optional)")

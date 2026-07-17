# ---- Base image -----------------------------------------------------
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# System deps kept minimal; sentence-transformers pulls torch which is large,
# so it is installed only when present in requirements.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Install the light core by default (fast build, no torch) - the image runs the
# full app in local mode. To bake in the cloud path (OpenAI + Qdrant + real
# embeddings), swap the install line for `requirements-cloud.txt`.
COPY requirements.txt requirements-cloud.txt ./
RUN pip install -r requirements.txt

COPY src/ ./src/
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY eval/ ./eval/
COPY data/ ./data/

# Generate the dataset at build time so the image is runnable out of the box.
RUN python scripts/generate_data.py --rows 5000 --out data/rag_formatted_data.xlsx

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]

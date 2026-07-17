"""Index the IPDR dataset into Qdrant for semantic search.

Only needed for the cloud/managed vector path. The app also has an in-memory
store that indexes on demand, so this script is optional.

Usage:
    PYTHONPATH=src python scripts/index_qdrant.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402

from ipdr_agent import load_settings  # noqa: E402
from ipdr_agent.embeddings import build_embedder  # noqa: E402
from ipdr_agent.vector.qdrant_store import QdrantVectorStore  # noqa: E402


def main() -> int:
    settings = load_settings()
    if not settings.has_qdrant:
        print("QDRANT_URL / QDRANT_API_KEY not set - nothing to do.")
        return 1

    df = pd.read_excel(settings.data_path)
    embedder = build_embedder(settings.embedding_model)

    store = QdrantVectorStore(settings.qdrant_url, settings.qdrant_api_key,
                              settings.qdrant_collection, embedder.dim)
    store.ensure_collection()

    texts = df["rag_text"].astype(str).tolist()
    vectors = embedder.encode(texts)
    payloads = df.astype({"timestamp": str}).to_dict("records")
    store.upsert(list(range(len(payloads))), vectors, payloads)

    print(f"Indexed {store.count():,} points into '{settings.qdrant_collection}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

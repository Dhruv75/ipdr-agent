"""In-memory vector store (offline fallback).

Brute-force cosine similarity with optional exact-match payload filtering. Fine
for a few thousand rows; for production scale you would use the Qdrant store,
which does ANN + server-side filtering.
"""
from __future__ import annotations

import numpy as np

from .base import SearchHit, VectorStore


class InMemoryVectorStore(VectorStore):
    def __init__(self) -> None:
        self._vectors: np.ndarray | None = None
        self._payloads: list[dict] = []

    def upsert(self, ids: list[int], vectors: np.ndarray,
               payloads: list[dict]) -> None:
        vectors = np.asarray(vectors, dtype=np.float32)
        if self._vectors is None:
            self._vectors = vectors
        else:
            self._vectors = np.vstack([self._vectors, vectors])
        self._payloads.extend(payloads)

    def count(self) -> int:
        return 0 if self._vectors is None else len(self._payloads)

    def search(self, query_vector: np.ndarray, limit: int = 30,
               filters: dict | None = None) -> list[SearchHit]:
        if self._vectors is None or len(self._payloads) == 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        # vectors are already L2-normalised, so dot product == cosine similarity.
        scores = self._vectors @ q

        candidate_idx = range(len(self._payloads))
        if filters:
            candidate_idx = [
                i for i in candidate_idx
                if all(str(self._payloads[i].get(k)) == str(v)
                       for k, v in filters.items())
            ]
        ranked = sorted(candidate_idx, key=lambda i: scores[i], reverse=True)[:limit]
        return [SearchHit(score=float(scores[i]), payload=self._payloads[i])
                for i in ranked]

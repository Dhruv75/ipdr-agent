"""Qdrant-backed vector store (production path).

Demonstrates server-side payload filtering, which is the reason to prefer a
real vector DB over the in-memory store: you can pre-filter to a source_ip or
data_type *before* the ANN search instead of scanning everything.
"""
from __future__ import annotations

import numpy as np

from .base import SearchHit, VectorStore


class QdrantVectorStore(VectorStore):
    def __init__(self, url: str, api_key: str, collection: str, dim: int):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._client = QdrantClient(url=url, api_key=api_key)
        self._collection = collection
        self._dim = dim
        self._VectorParams = VectorParams
        self._Distance = Distance

    def ensure_collection(self) -> None:
        self._client.recreate_collection(
            collection_name=self._collection,
            vectors_config=self._VectorParams(
                size=self._dim, distance=self._Distance.COSINE
            ),
        )

    def upsert(self, ids: list[int], vectors: np.ndarray,
               payloads: list[dict]) -> None:
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(id=int(i), vector=v.tolist(), payload=p)
            for i, v, p in zip(ids, vectors, payloads)
        ]
        for start in range(0, len(points), 100):
            self._client.upsert(self._collection, points[start:start + 100])

    def count(self) -> int:
        try:
            return self._client.count(self._collection).count
        except Exception:
            return 0

    def search(self, query_vector: np.ndarray, limit: int = 30,
               filters: dict | None = None) -> list[SearchHit]:
        query_filter = None
        if filters:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            query_filter = Filter(must=[
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ])
        res = self._client.query_points(
            collection_name=self._collection,
            query=np.asarray(query_vector, dtype=np.float32).tolist(),
            limit=limit,
            query_filter=query_filter,
        )
        return [SearchHit(score=float(p.score or 0.0), payload=p.payload or {})
                for p in res.points]

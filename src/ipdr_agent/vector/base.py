"""Vector store abstraction for semantic retrieval."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class SearchHit:
    score: float
    payload: dict


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, ids: list[int], vectors: np.ndarray,
               payloads: list[dict]) -> None:
        ...

    @abstractmethod
    def search(self, query_vector: np.ndarray, limit: int = 30,
               filters: dict | None = None) -> list[SearchHit]:
        ...

    @abstractmethod
    def count(self) -> int:
        ...

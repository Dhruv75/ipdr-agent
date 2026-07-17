"""Text embedding backends for semantic (RAG) search.

* SentenceTransformerEmbedder - the real model (all-MiniLM-L6-v2, 384d). Runs
  fully locally; needs weights downloaded once but no paid API.
* HashingEmbedder - a dependency-free, deterministic fallback used in CI or when
  sentence-transformers is unavailable. Uses whole words plus character 3-grams
  so it still gives useful fuzzy overlap.
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

import numpy as np

_WORD_RE = re.compile(r"[a-z0-9]+")


class Embedder(ABC):
    dim: int

    @abstractmethod
    def encode(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array of L2-normalised embeddings."""

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class SentenceTransformerEmbedder(Embedder):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, normalize_embeddings=True,
                                  show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


class HashingEmbedder(Embedder):
    """Deterministic hashed-feature embedding. Zero dependencies.

    Features = whole words + character 3-grams of each word. The char n-grams
    give fuzzy overlap ("exfiltration" ~ "exfiltrate", "uploads" ~ "upload",
    words split out of "Data_Exfiltration"), which makes offline retrieval
    usefully better than plain bag-of-words while staying dependency-free.
    """

    def __init__(self, dim: int = 384, char_ngram: int = 3):
        self.dim = dim
        self.char_ngram = char_ngram

    def _features(self, text: str):
        for w in _WORD_RE.findall(text.lower()):
            yield w
            padded = "#" + w + "#"
            for i in range(len(padded) - self.char_ngram + 1):
                yield padded[i:i + self.char_ngram]

    def _embed_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for feat in self._features(text):
            h = int(hashlib.md5(feat.encode()).hexdigest(), 16)
            vec[h % self.dim] += 1.0
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._embed_one(t) for t in texts]).astype(np.float32)


def build_embedder(model_name: str, prefer_local_model: bool = True) -> Embedder:
    """Pick the best available embedder, degrading gracefully."""
    if prefer_local_model:
        try:
            return SentenceTransformerEmbedder(model_name)
        except Exception:
            pass
    return HashingEmbedder()

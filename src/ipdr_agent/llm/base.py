"""LLM provider abstraction.

The rest of the codebase depends only on this small interface, so swapping
OpenAI for Azure/Anthropic/a local model is a one-file change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, system: str, user: str, *, json_mode: bool = False,
             temperature: float = 0.0, max_tokens: int | None = None) -> str:
        """Return the assistant text for a single-turn system+user prompt."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

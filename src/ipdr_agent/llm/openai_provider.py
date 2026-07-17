"""OpenAI-backed :class:`LLMProvider`."""
from __future__ import annotations

from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        # Imported lazily so the package works without the openai dependency
        # installed (offline mode never touches this class).
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    @property
    def name(self) -> str:
        return f"openai:{self._model}"

    def chat(self, system: str, user: str, *, json_mode: bool = False,
             temperature: float = 0.0, max_tokens: int | None = None) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

"""Thin LLM abstraction so the rest of the app never imports a vendor SDK directly (and tests can fake it)."""
from typing import Protocol

from ..config import settings


class LLMError(Exception):
    """The model call failed or returned something unusable."""


class LLM(Protocol):
    def complete(self, system: str, messages: list[dict], max_tokens: int = 800) -> str: ...


class AnthropicLLM:
    def __init__(self, api_key: str, model: str):
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=30.0, max_retries=1)
        self._model = model

    def complete(self, system: str, messages: list[dict], max_tokens: int = 800) -> str:
        try:
            resp = self._client.messages.create(
                model=self._model, max_tokens=max_tokens, system=system, messages=messages
            )
        except self._anthropic.APIError as e:  # network, auth, rate limit, overload...
            raise LLMError(str(e)) from e
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if not text:
            raise LLMError("Empty response from model")
        return text


_instance: LLM | None = None


def get_llm() -> LLM | None:
    """FastAPI dependency. None means no API key is configured; callers fall back to rule-based logic."""
    global _instance
    if not settings.anthropic_api_key:
        return None
    if _instance is None:
        _instance = AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model)
    return _instance

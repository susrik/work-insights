"""LLM client abstraction.

``LLMClient`` is the only interface the rest of the code sees. ``OpenAILLM``
talks to any OpenAI-compatible endpoint; ``FakeLLM`` is used in tests.
``complete_json`` is the shared "ask for JSON, validate, retry once" helper.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from insights.config import LLMConfig

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass
class LLMResult:
    text: str
    tokens_used: int = 0
    elapsed_ms: int = 0


class LLMClient(Protocol):
    def complete(self, prompt: str, system: str | None = None, smart: bool = False) -> LLMResult:
        """Return the completion for ``prompt``. ``smart`` selects the stronger model."""
        ...


class OpenAILLM:
    def __init__(self, config: LLMConfig, http_client: Any = None) -> None:
        from openai import OpenAI

        self._config = config
        self._client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            http_client=http_client,
            timeout=config.request_timeout,
        )

    def complete(self, prompt: str, system: str | None = None, smart: bool = False) -> LLMResult:
        messages: list[Any] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        model = self._config.smart_model if smart else self._config.model
        started = time.monotonic()
        response = self._client.chat.completions.create(model=model, messages=messages)
        elapsed = int((time.monotonic() - started) * 1000)
        text = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0
        return LLMResult(text=text, tokens_used=tokens, elapsed_ms=elapsed)


@dataclass
class FakeLLM:
    """Scripted responses; the last one repeats. Records every call."""

    responses: list[str] = field(default_factory=lambda: ["fake response"])
    calls: list[dict[str, Any]] = field(default_factory=list)
    tokens_per_call: int = 10

    def complete(self, prompt: str, system: str | None = None, smart: bool = False) -> LLMResult:
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append({"prompt": prompt, "system": system, "smart": smart})
        return LLMResult(text=self.responses[index], tokens_used=self.tokens_per_call)


def strip_fence(text: str) -> str:
    return _FENCE.sub("", text).strip()


def is_retryable(exc: BaseException) -> bool:
    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
    except ImportError:  # pragma: no cover
        return False
    if isinstance(exc, APITimeoutError | APIConnectionError | RateLimitError):
        return True
    return isinstance(exc, APIStatusError) and exc.status_code in _RETRYABLE_STATUS


def backoff(attempt: int) -> float:
    return float(min(2**attempt, 8))


class LLMJSONError(RuntimeError):
    pass


def complete_json[T: BaseModel](
    llm: LLMClient,
    prompt: str,
    model: type[T],
    system: str | None = None,
    smart: bool = False,
    attempts: int = 3,
    on_call: Callable[[LLMResult], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    """Ask for JSON matching ``model``; retry on invalid JSON or transient errors.

    Returns the parsed model and the total tokens used across attempts.
    ``on_call`` receives every raw result (for job logging).
    """
    tokens = 0
    current_prompt = prompt
    last_error = ""
    for attempt in range(attempts):
        try:
            result = llm.complete(current_prompt, system=system, smart=smart)
        except Exception as e:
            if is_retryable(e) and attempt < attempts - 1:
                sleep(backoff(attempt))
                continue
            raise
        tokens += result.tokens_used
        if on_call:
            on_call(result)
        try:
            return model.model_validate_json(strip_fence(result.text)), tokens
        except ValidationError as e:
            last_error = str(e).splitlines()[0]
            current_prompt = (
                f"{prompt}\n\nYour previous reply was not valid ({last_error}). "
                "Reply with JSON only, matching the requested schema."
            )
    raise LLMJSONError(f"LLM did not return valid JSON after {attempts} attempts: {last_error}")

from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import APIStatusError, APITimeoutError
from pydantic import BaseModel

from insights.config import LLMConfig
from insights.core.llm import (
    FakeLLM,
    LLMJSONError,
    LLMResult,
    OpenAILLM,
    backoff,
    complete_json,
    is_retryable,
    strip_fence,
)


class Reply(BaseModel):
    answer: int


def test_fake_llm_repeats_last_response() -> None:
    llm = FakeLLM(responses=["a", "b"])
    assert llm.complete("1").text == "a"
    assert llm.complete("2", system="s", smart=True).text == "b"
    assert llm.complete("3").text == "b"
    assert llm.calls[1] == {"prompt": "2", "system": "s", "smart": True}


def test_strip_fence() -> None:
    assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fence('{"a": 1}') == '{"a": 1}'


def test_complete_json_retries_on_invalid_then_succeeds() -> None:
    llm = FakeLLM(responses=["not json", '{"answer": 42}'])
    seen: list[LLMResult] = []
    reply, tokens = complete_json(llm, "q", Reply, on_call=seen.append)
    assert reply.answer == 42
    assert tokens == 20
    assert len(seen) == 2
    assert "previous reply was not valid" in llm.calls[1]["prompt"]


def test_complete_json_gives_up() -> None:
    llm = FakeLLM(responses=["nope"])
    with pytest.raises(LLMJSONError):
        complete_json(llm, "q", Reply, attempts=2)


class _Flaky:
    def __init__(self, failures: int, exc: Exception) -> None:
        self.failures = failures
        self.exc = exc
        self.calls = 0

    def complete(self, prompt: str, system: str | None = None, smart: bool = False) -> LLMResult:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc
        return LLMResult(text='{"answer": 1}', tokens_used=5)


def test_complete_json_retries_transient_errors() -> None:
    llm = _Flaky(1, APITimeoutError(request=httpx.Request("POST", "http://x")))
    slept: list[float] = []
    reply, _ = complete_json(llm, "q", Reply, sleep=slept.append)
    assert reply.answer == 1
    assert slept == [1.0]


def test_complete_json_raises_non_retryable() -> None:
    llm = _Flaky(5, RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        complete_json(llm, "q", Reply, sleep=lambda _: None)


def test_complete_json_raises_when_retries_exhausted() -> None:
    llm = _Flaky(5, APITimeoutError(request=httpx.Request("POST", "http://x")))
    with pytest.raises(APITimeoutError):
        complete_json(llm, "q", Reply, attempts=2, sleep=lambda _: None)


def test_is_retryable_and_backoff() -> None:
    request = httpx.Request("POST", "http://x")
    assert is_retryable(APITimeoutError(request=request))
    response_503 = httpx.Response(503, request=request)
    assert is_retryable(APIStatusError("x", response=response_503, body=None))
    response_400 = httpx.Response(400, request=request)
    assert not is_retryable(APIStatusError("x", response=response_400, body=None))
    assert not is_retryable(ValueError("x"))
    assert [backoff(i) for i in range(5)] == [1, 2, 4, 8, 8]


def test_openai_llm_wire_format() -> None:
    """Exercise the real client against a mock transport (no network)."""
    captured: dict[str, object] = {}
    hx = _openai_httpx()

    def handler(request: Any) -> Any:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return hx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "smart",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        )

    http_client = hx.Client(transport=hx.MockTransport(handler))
    llm = OpenAILLM(
        LLMConfig(base_url="http://llm.test/v1", api_key="k", model="cheap", smart_model="smart"),
        http_client=http_client,
    )
    result = llm.complete("hi", system="be brief", smart=True)
    assert result.text == "hello"
    assert result.tokens_used == 7
    assert "chat/completions" in str(captured["url"])
    assert '"model":"smart"' in str(captured["body"]).replace(" ", "")
    assert "be brief" in str(captured["body"])


def _openai_httpx() -> Any:
    """The openai SDK (>=3) vendors its own httpx as ``httpx2``; the mock
    transport and response must come from whichever module it uses."""
    try:
        import httpx2  # type: ignore[import-not-found]
    except ImportError:
        return httpx
    return httpx2

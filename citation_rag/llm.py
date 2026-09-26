"""Canonical Ollama HTTP client (integration-1 item 2).

One client, one interface, shared by four callers that each previously had
their own copy or their own client class: the company router
(`citation_rag.search.router`), the listwise reranker
(`citation_rag.rerank.llm_listwise`), the answer stage
(`citation_rag.answer.llm`), and the judge (`citation_rag.evals.judge`).

Every one of the four sends a single, fully-built prompt string and reads
back one JSON response -- none needs a multi-turn conversation -- so one
`complete(prompt) -> str` call over Ollama's `POST /api/generate` covers all
four. Options: `think` (Ollama's `"think"` field, sent only when not
`None`, since a model that does not support thinking should not receive the
field at all), `temperature`, `num_ctx`, `format` (defaults to `"json"`,
since every caller here wants JSON back), and `timeout` (default 900
seconds: Qwen3 8B and gpt-oss:20b generations on this machine's Ollama can
run for minutes). After a call, `last_input_tokens`, `last_output_tokens`,
and `last_wall_time_s` hold the response's own `prompt_eval_count` /
`eval_count` fields and the measured wall time.

`LLMClient` is the minimal protocol every one of the four modules programs
against (a `model` attribute, a `complete(prompt)` method). `FakeLLMClient`
is the shared test double: it never makes a network call, and every test in
every wave that touches an LLM call uses it instead of `OllamaClient`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

DEFAULT_TIMEOUT = 900.0

__all__ = ["LLMClient", "FakeLLMClient", "OllamaClient", "DEFAULT_TIMEOUT"]


class LLMClient(Protocol):
    model: str

    def complete(self, prompt: str) -> str: ...  # pragma: no cover - protocol


class FakeLLMClient:
    """Test double for LLMClient.

    `responses` is either a single string (always returned), a list of
    strings (consumed in order, one per call), or a callable(prompt) -> str.
    """

    def __init__(self, responses: "str | list[str] | Callable[[str], str]", model: str = "fake"):
        self.responses = responses
        self.model = model
        self._queue = list(responses) if isinstance(responses, list) else None

    def complete(self, prompt: str) -> str:
        if callable(self.responses):
            return self.responses(prompt)
        if isinstance(self.responses, str):
            return self.responses
        if self._queue is not None:
            if not self._queue:
                raise RuntimeError("FakeLLMClient response queue exhausted")
            return self._queue.pop(0)
        raise RuntimeError("FakeLLMClient has no usable responses")


@dataclass
class OllamaClient:
    """Real client over the local Ollama HTTP API (`POST /api/generate`).

    Canonical client for the router, the listwise reranker, the answer
    stage, and the judge. Never called in any wave's unit tests -- every
    test uses `FakeLLMClient` (or, for the judge, `FakeJudge`, which wraps
    the same idea).
    """

    model: str = "qwen3:8b"
    temperature: float = 0.0
    think: bool | None = None
    num_ctx: int | None = None
    format: str | None = "json"
    timeout: float = DEFAULT_TIMEOUT
    base_url: str | None = None

    # Set after each `complete()` call, from the response fields.
    last_input_tokens: int | None = field(default=None, init=False, repr=False)
    last_output_tokens: int | None = field(default=None, init=False, repr=False)
    last_wall_time_s: float | None = field(default=None, init=False, repr=False)

    def complete(self, prompt: str) -> str:
        import httpx

        url = self.base_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        options: dict = {"temperature": self.temperature}
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": options,
        }
        if self.format is not None:
            payload["format"] = self.format
        if self.think is not None:
            payload["think"] = self.think

        start = time.perf_counter()
        resp = httpx.post(f"{url}/api/generate", json=payload, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        self.last_wall_time_s = time.perf_counter() - start
        self.last_input_tokens = data.get("prompt_eval_count")
        self.last_output_tokens = data.get("eval_count")
        return data["response"]

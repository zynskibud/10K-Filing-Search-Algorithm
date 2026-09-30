"""The answer-stage LLM client: `OllamaChat` over the shared Ollama client.

Contract (wave 7a, point 3): `OllamaChat(model="qwen3:8b", thinking, temperature=0,
num_ctx=32768, timeout=900)`, `format: "json"` for the final answer, and the
`think` option to toggle thinking. Records input and output token counts and
wall time from the response fields.

Integration-1 item 2: the actual HTTP call is now made by
`citation_rag.llm.OllamaClient`, the one client shared by the router, the
listwise reranker, the answer stage, and the judge (previously this module
had its own copy, calling `POST /api/chat` directly). `OllamaChat` keeps its
own constructor shape (`thinking`, not `think`; `qwen3:8b`/`32768`/`900`
defaults) since `citation_rag.answer.run_all` already builds it that way;
it is now a thin wrapper around the shared client instead of its own client.

`LLMClient` and `FakeLLMClient` are reused as-is from `citation_rag.llm`.
Every test in this wave uses `FakeLLMClient` only; `OllamaChat` is never
called here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from citation_rag.llm import FakeLLMClient, LLMClient, OllamaClient  # noqa: F401  (re-exported)

__all__ = ["LLMClient", "FakeLLMClient", "OllamaChat"]


@dataclass
class OllamaChat:
    """Answer-stage client: `citation_rag.llm.OllamaClient` under the
    constructor shape the answer pipeline already uses. Never called in
    this wave's tests."""

    model: str = "qwen3:8b"
    thinking: bool = True
    temperature: float = 0.0
    num_ctx: int = 32768
    timeout: float = 900.0
    base_url: str | None = None

    _client: OllamaClient | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = OllamaClient(
            model=self.model,
            temperature=self.temperature,
            think=self.thinking,
            num_ctx=self.num_ctx,
            format="json",
            timeout=self.timeout,
            base_url=self.base_url,
        )

    @property
    def last_input_tokens(self) -> int | None:
        return self._client.last_input_tokens

    @property
    def last_output_tokens(self) -> int | None:
        return self._client.last_output_tokens

    @property
    def last_thinking_chars(self) -> int | None:
        return self._client.last_thinking_chars

    @property
    def last_wall_time_s(self) -> float | None:
        return self._client.last_wall_time_s

    def complete(self, prompt: str) -> str:
        return self._client.complete(prompt)

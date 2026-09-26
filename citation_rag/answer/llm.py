"""The answer-stage LLM client: `OllamaChat` over the Ollama chat API.

Contract (wave 7a, point 3): `OllamaChat(model="qwen3:8b", thinking, temperature=0,
num_ctx=32768, timeout=900)`, using `POST /api/chat` with `format: "json"` for
the final answer, and the `think` option to toggle thinking. Records input
and output token counts and wall time from the response fields.

`LLMClient` and `FakeLLMClient` are reused as-is from
`citation_rag.search.router` (wave 5a already built them for the company
router, and the contract says to import rather than duplicate). Every test
in this wave uses `FakeLLMClient` only; `OllamaChat` is never called here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from citation_rag.search.router import FakeLLMClient, LLMClient  # noqa: F401  (re-exported)

__all__ = ["LLMClient", "FakeLLMClient", "OllamaChat"]


@dataclass
class OllamaChat:
    """Real client over the local Ollama chat API. Never called in this wave's tests."""

    model: str = "qwen3:8b"
    thinking: bool = True
    temperature: float = 0.0
    num_ctx: int = 32768
    timeout: float = 900.0
    base_url: str | None = None

    # Set after each `complete()` call, from the response fields.
    last_input_tokens: int | None = field(default=None, init=False, repr=False)
    last_output_tokens: int | None = field(default=None, init=False, repr=False)
    last_wall_time_s: float | None = field(default=None, init=False, repr=False)

    def complete(self, prompt: str) -> str:
        import time

        import httpx

        url = self.base_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        start = time.perf_counter()
        resp = httpx.post(
            f"{url}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "format": "json",
                "stream": False,
                "think": self.thinking,
                "options": {
                    "temperature": self.temperature,
                    "num_ctx": self.num_ctx,
                },
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self.last_wall_time_s = time.perf_counter() - start
        self.last_input_tokens = data.get("prompt_eval_count")
        self.last_output_tokens = data.get("eval_count")
        return data["message"]["content"]

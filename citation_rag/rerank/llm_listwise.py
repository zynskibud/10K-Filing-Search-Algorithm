"""Listwise LLM reranker: one Qwen3 8B call reorders all the candidates.

The prompt lists the question and every candidate as `[1] ... [N]` (each
truncated to 300 tokens on the bge-small ruler, the same tokenizer
`citation_rag.chunk.tokens` uses for chunk sizes elsewhere in the project),
and asks for JSON: a list of candidate numbers from most to least relevant.
The response is parsed; any candidate number missing from the list (or the
whole response, if it does not parse) falls back to fusion order.

The LLM client is `citation_rag.search.router`'s (`LLMClient` protocol,
`FakeLLMClient`, `OllamaClient`) -- imported here, not duplicated, per the
contract. Ollama's "thinking off" switch (`"think": false`) is a call
option on the client, not on this reranker; `router.OllamaClient.complete`
does not yet expose it (it only sends `temperature`). That is the search
agent's file, so it is not edited here -- this reranker only ever calls
`client.complete(prompt)`, so it will pick up thinking-control transparently
whenever the shared client gains it. No Ollama calls happen in this wave's
tests: `FakeLLMClient` stands in.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from citation_rag.rerank.base import RerankedResult
from citation_rag.search.router import LLMClient  # re-exported for callers; not duplicated

TRUNCATE_TOKENS = 300

LISTWISE_PROMPT_TEMPLATE = """You rank candidate passages from SEC 10-K filings by how well each one \
answers the question. Read the question, then read each numbered candidate.

Question: {question}

Candidates:
{candidates_block}

Reply with JSON only, in this exact shape: a list of every candidate number, \
ordered from most relevant to least relevant, using each number exactly once:
{{"order": [<int>, ...]}}
"""


def _truncate(text: str, max_tokens: int) -> str:
    from citation_rag.chunk.tokens import token_offsets  # deferred: loads a tokenizer

    offsets = token_offsets(text)
    if len(offsets) <= max_tokens:
        return text
    end = offsets[max_tokens - 1][1]
    return text[:end]


@dataclass
class ListwiseReranker:
    client: LLMClient
    name: str = "llm_listwise"
    truncate_tokens: int = TRUNCATE_TOKENS
    prompt_template: str = LISTWISE_PROMPT_TEMPLATE
    last_wall_ms: float = field(default=0.0, init=False, repr=False)

    def _build_prompt(self, question: str, candidates: Sequence[Any]) -> str:
        lines = [
            f"[{i}] {_truncate(c.text, self.truncate_tokens)}"
            for i, c in enumerate(candidates, start=1)
        ]
        return self.prompt_template.format(question=question, candidates_block="\n\n".join(lines))

    def _parse_order(self, raw: str, n: int) -> list[int]:
        """0-indexed order over range(n). A response that does not parse into
        a list falls back to fusion order entirely; a response that parses
        but omits or duplicates some numbers keeps the valid ones in the
        order given and appends whatever is missing, in fusion order."""
        try:
            data = json.loads(raw)
            raw_order = data["order"]
            if not isinstance(raw_order, list):
                raise ValueError("order is not a list")
        except Exception:
            return list(range(n))

        seen: set[int] = set()
        order: list[int] = []
        for num in raw_order:
            if not isinstance(num, int):
                continue
            idx = num - 1
            if 0 <= idx < n and idx not in seen:
                order.append(idx)
                seen.add(idx)
        for idx in range(n):
            if idx not in seen:
                order.append(idx)
        return order

    def rerank(self, question: str, candidates: Sequence[Any], top: int) -> list[RerankedResult]:
        candidates = list(candidates)
        t0 = time.perf_counter()
        if not candidates:
            self.last_wall_ms = (time.perf_counter() - t0) * 1000.0
            return []

        prompt = self._build_prompt(question, candidates)
        raw = self.client.complete(prompt)
        order = self._parse_order(raw, len(candidates))
        self.last_wall_ms = (time.perf_counter() - t0) * 1000.0

        n = len(candidates)
        return [
            RerankedResult.from_result(candidates[idx], float(n - rank))
            for rank, idx in enumerate(order[:top])
        ]

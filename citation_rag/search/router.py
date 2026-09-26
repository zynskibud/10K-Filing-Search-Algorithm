"""Company router: decides which filings (CIKs) a question is about.

`route(question) -> {"companies": [cik, ...] | "general", "unresolved": [names]}`

- `OracleRouter(case)`: returns the golden case's own `companies` field
  unchanged. Used for experiments A, B, C (oracle routing; the router's own
  accuracy is measured separately, in wave 7).
- `LLMRouter(client, companies)`: the corpus's company list (name + ticker)
  is too long to put in the prompt, so the LLM is asked only to pull
  candidate company names/tickers out of the question. Our code then
  resolves each candidate against a small in-memory company directory by
  exact ticker match or by normalized-name match (with a small alias table
  for the vague/colloquial cases, e.g. "the iPhone maker"). A candidate that
  does not resolve is reported in `unresolved` rather than silently dropped,
  so the answer step can say "that company is not in our corpus."
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence


class Router(Protocol):
    def route(self, question: str) -> dict[str, Any]: ...  # pragma: no cover - protocol


@dataclass
class OracleRouter:
    """Returns the golden case's own `companies` field. No LLM call."""

    case: Any  # a GoldenCase, or any object/dict with a `companies` field

    def route(self, question: str) -> dict[str, Any]:
        if hasattr(self.case, "companies"):
            companies = self.case.companies
        else:
            companies = self.case["companies"]
        return {"companies": companies, "unresolved": []}


# --------------------------------------------------------------------------
# LLM clients
# --------------------------------------------------------------------------


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
    """Real client over the local Ollama HTTP API. Never called in this wave's tests."""

    model: str = "qwen3:8b"
    temperature: float = 0.0
    base_url: str | None = None

    def complete(self, prompt: str) -> str:
        import httpx

        url = self.base_url or os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        resp = httpx.post(
            f"{url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {"temperature": self.temperature},
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["response"]


# --------------------------------------------------------------------------
# LLMRouter
# --------------------------------------------------------------------------

EXTRACT_PROMPT_TEMPLATE = """You read one question about SEC 10-K filings. Pull out every company \
the question names, by any form: full legal name, short name, ticker \
symbol, or a clear description (for example "the iPhone maker").

Question: {question}

Reply with JSON only, in this exact shape:
{{"candidates": [{{"name": "<company name or description, or null>", \
"ticker": "<ticker symbol, or null>"}}], "none": <true if no company is named>}}

If the question names no company (a general question), reply with an empty \
"candidates" list and "none": true.
"""

# A small alias table for names the router should resolve to a corpus
# company's canonical, normalized name even when the LLM does not spell out
# the legal name itself. Judgment call: hand-picked from the plan's own
# example ("the iPhone maker" -> Apple); production would grow this table,
# or fold the resolution into the LLM prompt itself, from real router
# failures logged in wave 7.
DEFAULT_ALIASES: dict[str, str] = {
    "iphone maker": "apple",
    "the iphone maker": "apple",
}

_SUFFIXES = (
    " incorporated",
    " corporation",
    " company",
    " limited",
    " inc",
    " corp",
    " co",
    " ltd",
    " plc",
    " llc",
)


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, drop a trailing legal-entity suffix."""
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    name = re.sub(r"\s+", " ", name).strip()
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


@dataclass
class LLMRouter:
    client: LLMClient
    companies: Sequence[dict[str, str]]  # each: {"cik", "company", "ticker"}
    aliases: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIASES))
    prompt_template: str = EXTRACT_PROMPT_TEMPLATE

    def _by_ticker(self, ticker: str) -> str | None:
        ticker = ticker.strip().upper()
        if not ticker:
            return None
        for c in self.companies:
            if (c.get("ticker") or "").strip().upper() == ticker:
                return c["cik"]
        return None

    def _by_name(self, name: str) -> str | None:
        if not name:
            return None
        norm = normalize_name(name)
        if not norm:
            return None
        norm = self.aliases.get(norm, norm)
        for c in self.companies:
            if normalize_name(c.get("company", "")) == norm:
                return c["cik"]
        return None

    def _resolve(self, candidate: dict[str, Any]) -> str | None:
        ticker = candidate.get("ticker") or ""
        cik = self._by_ticker(ticker) if ticker else None
        if cik:
            return cik
        name = candidate.get("name") or ""
        return self._by_name(name)

    def route(self, question: str) -> dict[str, Any]:
        prompt = self.prompt_template.format(question=question)
        raw = self.client.complete(prompt)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Judgment call: an unparsable router response falls back to
            # "general" rather than raising, since a wrong route should
            # degrade the answer, not crash the pipeline.
            return {"companies": "general", "unresolved": []}

        candidates = data.get("candidates") or []
        if not candidates or data.get("none"):
            return {"companies": "general", "unresolved": []}

        resolved: list[str] = []
        unresolved: list[str] = []
        for cand in candidates:
            cik = self._resolve(cand)
            if cik is not None:
                if cik not in resolved:
                    resolved.append(cik)
            else:
                label = cand.get("name") or cand.get("ticker") or "unknown company"
                unresolved.append(label)

        return {"companies": resolved, "unresolved": unresolved}

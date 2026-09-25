"""LLM-judge module: four judgments (correctness, faithfulness, citation_support,
refusal), each backed by a versioned prompt file in evals/prompts/. Labels, not
scores. The reason is asked for before the label in every prompt.

No Ollama inference happens in this wave: OllamaJudge is written here for wave
7 to use, but every test in tests/test_evals.py uses FakeJudge only.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = PROJECT_ROOT / "evals" / "prompts"

CORRECTNESS_LABELS = {"correct", "partial", "incorrect"}
FAITHFULNESS_LABELS = {"supported", "not_supported", "contradicted"}
CITATION_LABELS = {"supports", "does_not_support"}
REFUSAL_LABELS_UNANSWERABLE = {"refused", "answered_anyway"}
REFUSAL_LABELS_ANSWERABLE = {"answered", "wrongly_refused"}


class JudgeParseError(ValueError):
    """Raised when a judge's raw response is not valid, well-formed JSON."""


class JudgeClient(Protocol):
    model: str

    def complete(self, prompt: str) -> str: ...  # pragma: no cover - protocol


@dataclass
class Judgment:
    label: str
    reason: str
    prompt_version: str
    model: str
    raw_response: str


class FakeJudge:
    """Test double for JudgeClient.

    `responses` is either:
      - a single string: always returned.
      - a list of strings: consumed in order, one per call.
      - a callable(prompt) -> str: full control.
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
        if self._queue:
            return self._queue.pop(0)
        raise RuntimeError("FakeJudge response queue exhausted")


@dataclass
class OllamaJudge:
    """Real judge client over the Ollama HTTP API. Not exercised in this wave."""

    model: str = "gpt-oss:20b"
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
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["response"]


def _load_prompt(name: str, version: str = "v1") -> str:
    path = PROMPTS_DIR / f"{name}.{version}.md"
    return path.read_text(encoding="utf-8")


def _parse_json_object(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JudgeParseError(f"response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeParseError("response JSON must be an object")
    return data


def _parse_label_response(raw: str, allowed_labels: set[str]) -> dict:
    data = _parse_json_object(raw)
    if "reason" not in data:
        raise JudgeParseError("response JSON is missing 'reason'")
    if "label" not in data:
        raise JudgeParseError("response JSON is missing 'label'")
    if data["label"] not in allowed_labels:
        raise JudgeParseError(f"label {data['label']!r} is not one of {sorted(allowed_labels)}")
    return data


def correctness(
    client: JudgeClient, question: str, reference_answer: str, answer: str, prompt_version: str = "v1"
) -> Judgment:
    template = _load_prompt("correctness", prompt_version)
    prompt = template.format(question=question, reference_answer=reference_answer, answer=answer)
    raw = client.complete(prompt)
    data = _parse_label_response(raw, CORRECTNESS_LABELS)
    return Judgment(data["label"], data["reason"], prompt_version, client.model, raw)


def split_claims(client: JudgeClient, answer: str, prompt_version: str = "v1") -> list[str]:
    """Step 1 of faithfulness: split the answer into atomic claims."""
    template = _load_prompt("faithfulness_split", prompt_version)
    prompt = template.format(answer=answer)
    raw = client.complete(prompt)
    data = _parse_json_object(raw)
    if "reason" not in data:
        raise JudgeParseError("response JSON is missing 'reason'")
    if "claims" not in data or not isinstance(data["claims"], list):
        raise JudgeParseError("response JSON must have a 'claims' list")
    return data["claims"]


def faithfulness_claim(
    client: JudgeClient, claim: str, retrieved_text: str, prompt_version: str = "v1"
) -> Judgment:
    """Step 2 of faithfulness: judge one claim against the retrieved text."""
    template = _load_prompt("faithfulness", prompt_version)
    prompt = template.format(claim=claim, retrieved_text=retrieved_text)
    raw = client.complete(prompt)
    data = _parse_label_response(raw, FAITHFULNESS_LABELS)
    return Judgment(data["label"], data["reason"], prompt_version, client.model, raw)


def faithfulness(
    client: JudgeClient, answer: str, retrieved_text: str, prompt_version: str = "v1"
) -> tuple[list[Judgment], float]:
    """Split the answer into claims, judge each against retrieved_text.

    Score = count(supported) / count(claims). Returns (per-claim judgments, score).
    """
    claims = split_claims(client, answer, prompt_version)
    judgments = [faithfulness_claim(client, c, retrieved_text, prompt_version) for c in claims]
    if not judgments:
        return judgments, 0.0
    score = sum(1 for j in judgments if j.label == "supported") / len(judgments)
    return judgments, score


def citation_support(
    client: JudgeClient, claim_sentence: str, cited_chunk_text: str, prompt_version: str = "v1"
) -> Judgment:
    template = _load_prompt("citation_support", prompt_version)
    prompt = template.format(claim_sentence=claim_sentence, cited_chunk_text=cited_chunk_text)
    raw = client.complete(prompt)
    data = _parse_label_response(raw, CITATION_LABELS)
    return Judgment(data["label"], data["reason"], prompt_version, client.model, raw)


def refusal(
    client: JudgeClient, question: str, answer: str, is_unanswerable: bool, prompt_version: str = "v1"
) -> Judgment:
    allowed = REFUSAL_LABELS_UNANSWERABLE if is_unanswerable else REFUSAL_LABELS_ANSWERABLE
    template = _load_prompt("refusal", prompt_version)
    prompt = template.format(question=question, answer=answer, is_unanswerable=is_unanswerable)
    raw = client.complete(prompt)
    data = _parse_label_response(raw, allowed)
    return Judgment(data["label"], data["reason"], prompt_version, client.model, raw)

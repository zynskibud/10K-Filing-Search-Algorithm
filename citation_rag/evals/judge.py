"""LLM-judge module: four judgments (correctness, faithfulness, citation_support,
refusal), each backed by a versioned prompt file in evals/prompts/. Labels, not
scores. The reason is asked for before the label in every prompt.

No Ollama inference happens in this wave: OllamaJudge is written here for wave
7 to use, but every test in tests/test_evals.py uses FakeJudge only.

Integration-1 item 3: `OllamaJudge` now makes its call through
`citation_rag.llm.OllamaClient` (the client shared with the router, the
listwise reranker, and the answer stage) with the shared 900s default
timeout, instead of its own inline `httpx` call.

CLI (wave 7, after calibration):
    uv run python -m citation_rag.evals.judge score --answers evals/answers/B_think.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from citation_rag.llm import DEFAULT_TIMEOUT, OllamaClient

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
    """Real judge client, built on the shared `citation_rag.llm.OllamaClient`.
    Not exercised in this wave: every test uses `FakeJudge`."""

    model: str = "gpt-oss:20b"
    temperature: float = 0.0
    base_url: str | None = None
    timeout: float = DEFAULT_TIMEOUT

    _client: OllamaClient | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = OllamaClient(
            model=self.model,
            temperature=self.temperature,
            base_url=self.base_url,
            timeout=self.timeout,
            format="json",
        )

    def complete(self, prompt: str) -> str:
        return self._client.complete(prompt)


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


# -- scoring an answers file (the wave 7 `score` command) --------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
ANSWERS_DIR = PROJECT_ROOT / "evals" / "answers"
JUDGED_DIR = PROJECT_ROOT / "evals" / "judged"


def _sentence_with_marker(answer: str, ref: int) -> str:
    marker = f"[{ref}]"
    for sentence in _SENTENCE_SPLIT.split(answer):
        if marker in sentence:
            return sentence
    return answer


def score_row(client: JudgeClient, row: dict, golden: "dict | None" = None) -> dict:
    """Judge one run_all answer row: correctness, refusal, and (when the row
    has context blocks) faithfulness and per-citation support. A judge reply
    that does not parse gives an `error` entry for that measure, not a crash."""
    golden = golden or {}
    answer_obj = row.get("answer")
    answer = answer_obj.get("answer", "") if isinstance(answer_obj, dict) else str(answer_obj or "")
    citations = answer_obj.get("citations", []) if isinstance(answer_obj, dict) else []
    question = row.get("question") or golden.get("question", "")
    reference = row.get("reference") or golden.get("answer", "")
    case_type = row.get("type") or golden.get("type")
    blocks = row.get("blocks") or []
    scores: dict = {}

    def attempt(key: str, fn):
        try:
            scores[key] = fn()
        except JudgeParseError as exc:
            scores[key] = {"error": str(exc)}

    def judgment_dict(j: Judgment) -> dict:
        return {"label": j.label, "reason": j.reason}

    attempt("correctness", lambda: judgment_dict(correctness(client, question, reference, answer)))
    attempt(
        "refusal",
        lambda: judgment_dict(refusal(client, question, answer, is_unanswerable=(case_type == "unanswerable"))),
    )
    if blocks:
        context = "\n\n".join(f"[{b.get('ref')}] {b.get('text', '')}" for b in blocks)

        def _faith() -> dict:
            judgments, score = faithfulness(client, answer, context)
            return {"score": score, "claims": [judgment_dict(j) for j in judgments]}

        attempt("faithfulness", _faith)
        by_ref = {b.get("ref"): b for b in blocks}
        support = []
        for c in citations:
            block = by_ref.get(c.get("ref"))
            if block is None:
                support.append({"ref": c.get("ref"), "label": "does_not_support", "reason": "ref not in blocks"})
                continue
            try:
                j = citation_support(client, _sentence_with_marker(answer, c["ref"]), block.get("text", ""))
                support.append({"ref": c["ref"], **judgment_dict(j)})
            except JudgeParseError as exc:
                support.append({"ref": c.get("ref"), "error": str(exc)})
        scores["citation_support"] = support
    return {"id": row.get("id"), "type": case_type, "scores": scores}


def score_answers(
    answers_path: "str | Path",
    client: JudgeClient,
    out_path: "str | Path | None" = None,
    golden_path: "str | Path | None" = None,
) -> dict:
    """Judge every row of an answers file. Writes one JSON line per row to
    `out_path` (default `evals/judged/{run}.jsonl`) and returns a summary."""
    answers_path = Path(answers_path)
    golden: dict[str, dict] = {}
    if golden_path is not None and Path(golden_path).exists():
        for line in Path(golden_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                g = json.loads(line)
                golden[g["id"]] = g
    out_path = Path(out_path) if out_path else JUDGED_DIR / f"{answers_path.stem}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n = 0
    correct = 0
    faith_scores: list[float] = []
    with answers_path.open("r", encoding="utf-8") as f, out_path.open("w", encoding="utf-8") as out:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            result = score_row(client, row, golden.get(row.get("id")))
            result["run"] = answers_path.stem
            result["judge_model"] = getattr(client, "model", None)
            out.write(json.dumps(result) + "\n")
            n += 1
            if result["scores"].get("correctness", {}).get("label") == "correct":
                correct += 1
            faith = result["scores"].get("faithfulness", {})
            if "score" in faith:
                faith_scores.append(faith["score"])
    return {
        "run": answers_path.stem,
        "n": n,
        "correct_share": (correct / n) if n else None,
        "faithfulness_mean": (sum(faith_scores) / len(faith_scores)) if faith_scores else None,
        "out_path": str(out_path),
    }


def main(argv: "list[str] | None" = None, client: "JudgeClient | None" = None) -> int:
    """`score --answers evals/answers/X.jsonl [--golden ...] [--out ...] [--model ...]`.
    `client` is for tests (a `FakeJudge`); the CLI builds an `OllamaJudge`."""
    parser = argparse.ArgumentParser(prog="citation_rag.evals.judge")
    sub = parser.add_subparsers(dest="command", required=True)
    p_score = sub.add_parser("score", help="judge an answers file written by run_all")
    p_score.add_argument("--answers", required=True)
    p_score.add_argument("--split", default="dev")
    p_score.add_argument("--golden", default=None, help="default: evals/golden/{split}.jsonl")
    p_score.add_argument("--out", default=None, help="default: evals/judged/{run}.jsonl")
    p_score.add_argument("--model", default="gpt-oss:20b")
    args = parser.parse_args(argv)

    if args.command == "score":
        judge_client = client or OllamaJudge(model=args.model)
        golden = args.golden or str(PROJECT_ROOT / "evals" / "golden" / f"{args.split}.jsonl")
        summary = score_answers(args.answers, judge_client, out_path=args.out, golden_path=golden)
        print(json.dumps(summary))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

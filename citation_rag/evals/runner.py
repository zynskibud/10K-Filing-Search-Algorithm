"""Eval runner: runs a retriever over the golden set and writes a results log.

CLI:
    uv run python -m citation_rag.evals.runner --split dev --retriever random
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, NamedTuple, Sequence

from citation_rag.evals.golden import GoldenCase, load_golden
from citation_rag.evals.retrieval_metrics import (
    bootstrap_ci,
    hit_rank,
    is_hit,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_DIR = PROJECT_ROOT / "evals" / "golden"
DEFAULT_PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
FIXTURE_GOLDEN = PROJECT_ROOT / "tests" / "fixtures" / "golden_dev.jsonl"
FIXTURE_PARSED_DIR = PROJECT_ROOT / "tests" / "fixtures" / "parsed"
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"


class Result(NamedTuple):
    chunk_id: str
    text: str
    token_count: int
    section_id: str | None
    table_id: str | None
    page_start: int
    page_end: int
    accession_no: str


Retriever = Callable[[str, "list[str] | str"], list[Result]]


@dataclass
class RunRecord:
    run_id: str
    git_commit: str
    split: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    n_questions: int
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        def _metric(v: Any) -> Any:
            if isinstance(v, dict) and "ci95" in v:
                return {"value": v["value"], "ci95": [v["ci95"][0], v["ci95"][1]]}
            return v

        return {
            "run_id": self.run_id,
            "git_commit": self.git_commit,
            "split": self.split,
            "config": self.config,
            "metrics": {k: _metric(v) for k, v in self.metrics.items()},
            "n_questions": self.n_questions,
            "notes": self.notes,
        }


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{ts}-{uuid.uuid4().hex[:4]}"


def _case_evidences(case: GoldenCase) -> list[str]:
    """The list of evidence strings a case needs a hit against, or [] if unscoreable."""
    if case.evidence is None:
        return []
    if isinstance(case.evidence, list):
        return case.evidence
    return [case.evidence]


def _case_hit_at_k(evidences: list[str], results: Sequence[Result], k: int) -> bool:
    """True if every required evidence piece has a hit among the first k results."""
    if not evidences:
        return False
    window = results[:k]
    return all(any(is_hit(r.text, ev) for r in window) for ev in evidences)


def _case_mrr(evidences: list[str], results: Sequence[Result]) -> float:
    """1 / (rank needed to cover every evidence piece), or 0.0 if any piece is missing."""
    if not evidences:
        return 0.0
    ranks = [hit_rank(results, ev) for ev in evidences]
    if any(r is None for r in ranks):
        return 0.0
    return 1.0 / max(ranks)


def _case_hit_at_budget(
    evidences: list[str], results: Sequence[Result], budget_tokens: int
) -> bool:
    if not evidences:
        return False
    included: list[Result] = []
    total = 0
    for r in results:
        included.append(r)
        total += r.token_count
        if total > budget_tokens:
            break
    return all(any(is_hit(r.text, ev) for r in included) for ev in evidences)


def _load_parsed_corpus(parsed_dir: Path) -> list[dict]:
    filings = []
    if not parsed_dir.exists():
        return filings
    for fp in sorted(parsed_dir.glob("*.json")):
        try:
            filings.append(json.loads(fp.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return filings


def _corpus_results(filings: list[dict]) -> list[Result]:
    """Flatten every section and table across a set of parsed filings into Results."""
    results: list[Result] = []
    for filing in filings:
        accession_no = filing["accession_no"]
        for item in filing.get("items", []):
            for section in item.get("sections", []):
                text = section.get("text", "")
                results.append(
                    Result(
                        chunk_id=section["id"],
                        text=text,
                        token_count=max(1, len(text.split())),
                        section_id=section["id"],
                        table_id=None,
                        page_start=section.get("page_start"),
                        page_end=section.get("page_end"),
                        accession_no=accession_no,
                    )
                )
        for table in filing.get("tables", []):
            text = table.get("text", "")
            results.append(
                Result(
                    chunk_id=table["id"],
                    text=text,
                    token_count=max(1, len(text.split())),
                    section_id=table.get("section_id"),
                    table_id=table["id"],
                    page_start=table.get("page_start"),
                    page_end=table.get("page_end"),
                    accession_no=accession_no,
                )
            )
    return results


def make_random_retriever(
    parsed_dir: Path, seed: int = 0, max_results: int = 20
) -> Retriever:
    """A fake retriever, over the parsed corpus, for the smoke run and tests.

    It has no real relevance signal: it deterministically shuffles the corpus
    (optionally filtered to the named companies) per question and returns the
    first max_results as candidates.
    """
    filings = _load_parsed_corpus(parsed_dir)
    all_results = _corpus_results(filings)
    by_cik: dict[str, list[Result]] = {}
    for filing in filings:
        by_cik.setdefault(filing["cik"], []).extend(
            r for r in all_results if r.accession_no == filing["accession_no"]
        )

    def retriever(question: str, companies: "list[str] | str") -> list[Result]:
        pool = all_results
        if isinstance(companies, list) and companies:
            filtered = [r for cik in companies for r in by_cik.get(cik, [])]
            if filtered:
                pool = filtered
        rng = random.Random(f"{seed}:{question}")
        shuffled = pool[:]
        rng.shuffle(shuffled)
        return shuffled[:max_results]

    return retriever


BUILTIN_RETRIEVERS = {"random": make_random_retriever}


def run_eval(
    config: dict[str, Any],
    split: str,
    retriever: Retriever,
    *,
    golden_path: Path | str | None = None,
    parsed_dir: Path | str | None = None,
    unseal: bool = False,
    results_dir: Path | str | None = None,
) -> RunRecord:
    """Run a retriever over the golden set and write a results log line.

    split must be "dev"; "test" is refused unless unseal=True (wave 8 only).
    """
    if split not in ("dev", "test"):
        raise ValueError(f"split must be 'dev' or 'test', got {split!r}")
    if split == "test" and not unseal:
        raise PermissionError(
            "the test split is sealed until wave 8; pass unseal=True (CLI: --unseal) to run it"
        )

    notes = ""
    if golden_path is None:
        candidate = DEFAULT_GOLDEN_DIR / f"{split}.jsonl"
        if candidate.exists() and candidate.stat().st_size > 0:
            golden_path = candidate
        elif split == "dev":
            golden_path = FIXTURE_GOLDEN
            notes = "no evals/golden/dev.jsonl yet; used tests/fixtures/golden_dev.jsonl"
        else:
            raise FileNotFoundError(f"no golden file found at {candidate}")
    golden_path = Path(golden_path)

    if parsed_dir is None:
        if DEFAULT_PARSED_DIR.exists() and any(DEFAULT_PARSED_DIR.glob("*.json")):
            parsed_dir = DEFAULT_PARSED_DIR
        else:
            parsed_dir = FIXTURE_PARSED_DIR
            notes = (notes + "; " if notes else "") + "no parsed filings in data/parsed; used tests/fixtures/parsed"
    parsed_dir = Path(parsed_dir)

    cases = load_golden(golden_path)

    k = config.get("k", 8)
    top = config.get("top", k)
    budget_tokens = config.get("token_budget", 2000)

    recall_values: list[float] = []
    mrr_values: list[float] = []
    budget_values: list[float] = []
    scored_cases: list[GoldenCase] = []
    latencies_ms: list[float] = []
    detail_rows: list[dict[str, Any]] = []

    for case in cases:
        start = time.perf_counter()
        results = retriever(case.question, case.companies)
        latencies_ms.append((time.perf_counter() - start) * 1000)

        evidences = _case_evidences(case)
        top_ids = [r.chunk_id for r in results[:top]]
        rank: int | None = None

        if evidences:
            hit = _case_hit_at_k(evidences, results, k)
            recall_values.append(1.0 if hit else 0.0)
            mrr_values.append(_case_mrr(evidences, results))
            budget_values.append(1.0 if _case_hit_at_budget(evidences, results, budget_tokens) else 0.0)
            scored_cases.append(case)
            ranks = [hit_rank(results, ev) for ev in evidences]
            if all(r is not None for r in ranks):
                rank = max(ranks)

        detail_rows.append({"id": case.id, "hit_rank": rank, "top_result_ids": top_ids})

    metrics: dict[str, Any] = {}
    if recall_values:
        metrics[f"recall@{k}"] = {"value": sum(recall_values) / len(recall_values), "ci95": bootstrap_ci(recall_values)}
    if mrr_values:
        metrics["mrr"] = {"value": sum(mrr_values) / len(mrr_values), "ci95": bootstrap_ci(mrr_values)}
    if budget_values:
        metrics[f"recall@{budget_tokens}tok"] = {
            "value": sum(budget_values) / len(budget_values),
            "ci95": bootstrap_ci(budget_values),
        }

    from citation_rag.evals.retrieval_metrics import split_by_table, split_by_type

    if scored_cases:
        by_table = split_by_table(scored_cases, recall_values)
        for key, vals in by_table.items():
            if vals:
                metrics[f"recall@{k}_{key}"] = {"value": sum(vals) / len(vals), "ci95": bootstrap_ci(vals)}
        by_type = split_by_type(scored_cases, recall_values)
        for qtype, vals in by_type.items():
            if vals:
                metrics[f"recall@{k}_{qtype}"] = {"value": sum(vals) / len(vals), "ci95": bootstrap_ci(vals)}

    if latencies_ms:
        metrics["latency_ms_p50"] = median(latencies_ms)

    record = RunRecord(
        run_id=_new_run_id(),
        git_commit=_git_commit(),
        split=split,
        config=config,
        metrics=metrics,
        n_questions=len(cases),
        notes=notes,
    )

    out_dir = Path(results_dir) if results_dir is not None else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict()) + "\n")
    with (out_dir / f"{record.run_id}.jsonl").open("w", encoding="utf-8") as f:
        for row in detail_rows:
            f.write(json.dumps(row) + "\n")

    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.evals.runner")
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--retriever", default="random", choices=list(BUILTIN_RETRIEVERS))
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--token-budget", type=int, default=2000)
    parser.add_argument("--index", default="random")
    parser.add_argument("--search", default="random")
    parser.add_argument("--reranker", default="none")
    parser.add_argument("--router", action="store_true")
    parser.add_argument("--unseal", action="store_true", help="wave 8 only: allow running the test split")
    parser.add_argument("--golden-path", default=None)
    parser.add_argument("--parsed-dir", default=None)
    args = parser.parse_args(argv)

    parsed_dir = Path(args.parsed_dir) if args.parsed_dir else (
        DEFAULT_PARSED_DIR if DEFAULT_PARSED_DIR.exists() and any(DEFAULT_PARSED_DIR.glob("*.json")) else FIXTURE_PARSED_DIR
    )
    retriever = BUILTIN_RETRIEVERS[args.retriever](parsed_dir)

    config = {
        "index": args.index,
        "search": args.search,
        "reranker": args.reranker,
        "k": args.k,
        "top": args.top,
        "router": args.router,
        "token_budget": args.token_budget,
    }

    record = run_eval(
        config,
        args.split,
        retriever,
        golden_path=args.golden_path,
        parsed_dir=args.parsed_dir,
        unseal=args.unseal,
    )
    print(json.dumps(record.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

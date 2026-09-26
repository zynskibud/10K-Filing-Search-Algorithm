"""Run the four dev-split experiments (wave 7a contract, point 6).

CLI:
    uv run python -m citation_rag.answer.run_all --split dev \\
        --runs A_think A_nothink B_think B_nothink --config <winner.json> \\
        --dry-run

Run names: `A` = no-retrieval baseline (plan section 11, run A), `B` = our
RAG system (run B); `_think`/`_nothink` toggles Qwen3's thinking mode. Each
non-dry run writes one line per question to
`evals/answers/{run_name}.jsonl` (question id, answer JSON, citation check,
run_log id, latencies) and prints per-run counts (answered, refused,
citation-valid share). No judge call here -- that is
`citation_rag.evals.judge`, run separately in wave 7 after calibration.

This wave is LIGHT (no Ollama, no corpus reads beyond fixtures): the real
four Qwen runs happen later, GPU class, through `scripts/run.sh 7` after GO.
`--dry-run` is what this wave's tests and definition-of-done exercise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from citation_rag.answer.base import NoRerank
from citation_rag.answer.llm import OllamaChat
from citation_rag.answer.pipeline import answer
from citation_rag.evals.golden import GoldenCase, load_golden

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_DIR = PROJECT_ROOT / "evals" / "golden"
FIXTURE_GOLDEN = PROJECT_ROOT / "tests" / "fixtures" / "golden_dev.jsonl"
ANSWERS_DIR = PROJECT_ROOT / "evals" / "answers"

RUN_NAMES = ["A_think", "A_nothink", "B_think", "B_nothink"]


def _run_settings(run_name: str) -> dict[str, Any]:
    if run_name not in RUN_NAMES:
        raise ValueError(f"run name must be one of {RUN_NAMES}, got {run_name!r}")
    mode = "no_docs" if run_name.startswith("A") else "rag"
    thinking = run_name.endswith("_think")
    return {"mode": mode, "thinking": thinking}


def _golden_path(split: str) -> Path:
    candidate = DEFAULT_GOLDEN_DIR / f"{split}.jsonl"
    if candidate.exists() and candidate.stat().st_size > 0:
        return candidate
    if split == "dev" and FIXTURE_GOLDEN.exists():
        return FIXTURE_GOLDEN
    raise FileNotFoundError(f"no golden file found at {candidate}")


def _build_retriever(config: dict) -> Any:
    from citation_rag.search.retriever import Retriever

    return Retriever(
        index_name=config["index"],
        method=config["search"],
        k=config.get("k", 50),
        top=config.get("top", 8),
    )


def _build_reranker(config: dict) -> Any:
    """Build the winner reranker from wave 6's `citation_rag/rerank/`.

    Imported here, not duplicated. `none`, `cross_encoder`, `monot5`, and
    `colbert` need no extra collaborators beyond their own defaults. `mmr`
    (needs a chunk-vector lookup) and `llm_listwise` (needs an LLM client)
    need collaborators this function does not have, so they raise with a
    clear reason instead of guessing -- wiring those is the GPU-class
    execution step (`scripts/run.sh 7`), not this LIGHT wave.
    """
    reranker_name = config.get("reranker", "none")
    if reranker_name in (None, "none"):
        return NoRerank()

    if reranker_name == "cross_encoder":
        from citation_rag.rerank.cross_encoder import CrossEncoderReranker

        return CrossEncoderReranker()
    if reranker_name == "monot5":
        from citation_rag.rerank.monot5 import MonoT5Reranker

        return MonoT5Reranker()
    if reranker_name == "colbert":
        from citation_rag.rerank.colbert import ColbertReranker

        return ColbertReranker()
    if reranker_name in ("mmr", "llm_listwise"):
        raise NotImplementedError(
            f"reranker {reranker_name!r} needs extra collaborators "
            "(a chunk-vector lookup for mmr, an LLM client for llm_listwise) "
            "that run_all.py does not build; wire it in at execution time"
        )
    raise ValueError(f"unknown reranker: {reranker_name!r}")


def plan(split: str, run_names: list[str], winner_config: dict) -> list[dict]:
    """The list of {run_name, config, n_questions} this invocation would run."""
    golden_path = _golden_path(split)
    n_cases = sum(1 for line in golden_path.open("r", encoding="utf-8") if line.strip())
    out = []
    for run_name in run_names:
        config = {**winner_config, **_run_settings(run_name)}
        out.append(
            {
                "run_name": run_name,
                "config": config,
                "n_questions": n_cases,
                "golden_path": str(golden_path),
                "out_path": str(ANSWERS_DIR / f"{run_name}.jsonl"),
            }
        )
    return out


def run_one(
    run_name: str,
    cases: "list[GoldenCase]",
    winner_config: dict,
    *,
    retriever: Any,
    reranker: Any,
    out_dir: Path,
) -> dict:
    settings = _run_settings(run_name)
    config = {**winner_config, **settings}
    llm_client = OllamaChat(model=winner_config.get("model", "qwen3:8b"), thinking=settings["thinking"])

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_name}.jsonl"

    n_answered = 0
    n_refused = 0
    n_citation_valid = 0
    n_scored = 0

    with out_path.open("w", encoding="utf-8") as f:
        for case in cases:
            record = answer(
                case.question,
                config,
                case=case,
                retriever=retriever if config["mode"] == "rag" else None,
                reranker=reranker,
                llm_client=llm_client,
            )
            if record.answerable:
                n_answered += 1
            else:
                n_refused += 1
            if config["mode"] == "rag":
                n_scored += 1
                if record.citation_check.valid:
                    n_citation_valid += 1

            f.write(
                json.dumps(
                    {
                        "id": case.id,
                        "answer": {
                            "answer": record.answer,
                            "citations": record.citations,
                            "answerable": record.answerable,
                        },
                        "citation_check": vars(record.citation_check),
                        "run_log_id": record.run_log_id,
                        "latency_ms": record.latency_ms,
                    }
                )
                + "\n"
            )

    return {
        "run_name": run_name,
        "n_questions": len(cases),
        "answered": n_answered,
        "refused": n_refused,
        "citation_valid_share": (n_citation_valid / n_scored) if n_scored else None,
    }


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.answer.run_all")
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--runs", nargs="+", default=RUN_NAMES, choices=RUN_NAMES)
    parser.add_argument("--config", default=None, help="path to the winner config JSON (index, search, reranker, top, k)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    winner_config: dict[str, Any] = {}
    if args.config:
        winner_config = json.loads(Path(args.config).read_text(encoding="utf-8"))

    if args.dry_run:
        for item in plan(args.split, args.runs, winner_config):
            print(json.dumps(item))
        return 0

    cases = load_golden(_golden_path(args.split))

    retriever = _build_retriever(winner_config) if any(_run_settings(r)["mode"] == "rag" for r in args.runs) else None
    reranker = _build_reranker(winner_config)

    summaries = [
        run_one(run_name, cases, winner_config, retriever=retriever, reranker=reranker, out_dir=ANSWERS_DIR)
        for run_name in args.runs
    ]
    for s in summaries:
        print(json.dumps(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())

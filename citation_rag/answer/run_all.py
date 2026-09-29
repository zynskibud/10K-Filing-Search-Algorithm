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

The real four Qwen runs are GPU class and go through `scripts/run.sh 7` after
GO. `--dry-run` prints the plan and needs no database, model, or Ollama.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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


DEFAULT_WINNERS = PROJECT_ROOT / "runs" / "wave-5" / "winners.json"
WAVE7_DIR = PROJECT_ROOT / "runs" / "wave-7"
DEFAULT_RERANKER = "monot5"
CANDIDATE_K = 50
TOP_K = 8


def load_config(
    winners_path: "str | Path | None" = DEFAULT_WINNERS,
    reranker: str = DEFAULT_RERANKER,
    config_path: "str | Path | None" = None,
    *,
    required: bool = True,
) -> dict:
    """`{index, search, reranker, k, top}` from the wave 5 winners file plus a
    reranker name. `config_path` (a JSON object) overrides any key. With
    `required=False` a missing winners file gives just the reranker (dry runs)."""
    config: dict[str, Any] = {}
    path = Path(winners_path) if winners_path else None
    if path is not None and path.exists():
        winners = json.loads(path.read_text(encoding="utf-8"))
        config.update({"index": winners["index"], "search": winners["search"]})
    elif required and config_path is None:
        raise FileNotFoundError(f"winners file not found: {path}")
    config["reranker"] = reranker
    config.setdefault("k", CANDIDATE_K)
    config.setdefault("top", TOP_K)
    if config_path:
        config.update(json.loads(Path(config_path).read_text(encoding="utf-8")))
    return config


def _build_reranker(config: dict) -> Any:
    """The reranker named in `config["reranker"]`, built by wave 6's factory
    (`citation_rag.rerank.experiments.build_reranker`). `mmr` reads vectors
    from `chunks_{index}`; `llm_listwise` gets a shared Ollama client."""
    from citation_rag.rerank.experiments import build_reranker

    name = config.get("reranker") or "none"
    kwargs: dict[str, Any] = {}
    if config.get("reranker_device") and name in ("monot5", "cross_encoder", "colbert"):
        kwargs["device"] = config["reranker_device"]
    return build_reranker(name, config.get("index"), **kwargs)


def _build_retriever(config: dict, reranker: Any = None) -> Any:
    """Retriever as wave 6 measured it: `k` candidates in, reranked through
    the retriever hook, `top` out per company."""
    from citation_rag.search.retriever import Retriever

    top = config.get("top", TOP_K)
    return Retriever(
        index_name=config["index"],
        method=config["search"],
        k=config.get("k", CANDIDATE_K),
        top=top,
        per_company_top=top,
        general_cap=top,
        reranker=reranker,
    )


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
    reranker: Any = None,
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
                        "question": case.question,
                        "type": case.type,
                        "reference": case.answer,
                        "blocks": [
                            {"ref": b.ref, "section_id": b.section_id, "text": b.text} for b in record.blocks
                        ],
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
    parser.add_argument("--winners", default=str(DEFAULT_WINNERS), help="wave 5 winners.json (index, search)")
    parser.add_argument("--reranker", default=DEFAULT_RERANKER, help="reranker name (default monot5)")
    parser.add_argument("--config", default=None, help="optional JSON that overrides the resolved config")
    parser.add_argument("--out-dir", default=str(WAVE7_DIR), help="where config.json and summary.json go")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    winner_config = load_config(args.winners, args.reranker, args.config, required=not args.dry_run)

    if args.dry_run:
        for item in plan(args.split, args.runs, winner_config):
            print(json.dumps(item))
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(json.dumps(winner_config, indent=2), encoding="utf-8")

    cases = load_golden(_golden_path(args.split))

    reranker = _build_reranker(winner_config)
    retriever = (
        _build_retriever(winner_config, reranker)
        if any(_run_settings(r)["mode"] == "rag" for r in args.runs)
        else None
    )

    summaries = []
    for run_name in args.runs:
        summary = run_one(run_name, cases, winner_config, retriever=retriever, out_dir=ANSWERS_DIR)
        print(json.dumps(summary), flush=True)
        summaries.append(summary)
        (out_dir / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

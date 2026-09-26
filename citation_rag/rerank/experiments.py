"""Wave 6 experiment: compare the six rerankers on the winning index/search.

CLI:
    uv run python -m citation_rag.rerank.experiments \\
        --exp rerank --index <winner> --search <winner> --dry-run

Per the contract, nothing runs in this wave beyond `--dry-run`; the real run
happens later, through `scripts/run.sh 6`, once waves 4-5 have picked a
winning index and search method and the GPU class is free (the
`llm_listwise` reranker needs a live Ollama daemon).

Integration-1 item 1: `citation_rag.search.retriever.Retriever` now takes
`reranker=` directly (applied after RRF fusion, before the per-company cut
and the general cap), so `run_rerank_experiment` passes each reranker
straight into the `Retriever` it builds, instead of composing a wrapper
callable around a separately-configured retriever.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

RERANKER_NAMES = ["none", "mmr", "colbert", "cross_encoder", "monot5", "llm_listwise"]

# Candidate pool size before reranking (the plan's "top 50"), and the final
# cut after reranking (the plan's "top 8").
CANDIDATE_K = 50
TOP_K = 8


def build_reranker(name: str, **kwargs: Any):
    """Lazy factory: only imports (and, for model rerankers, loads weights
    for) the reranker actually requested."""
    if name == "none":
        from citation_rag.rerank.none import NoneReranker

        return NoneReranker()
    if name == "mmr":
        from citation_rag.rerank.mmr import MMRReranker

        return MMRReranker(**kwargs)
    if name == "colbert":
        from citation_rag.rerank.colbert import ColbertReranker

        return ColbertReranker(**kwargs)
    if name == "cross_encoder":
        from citation_rag.rerank.cross_encoder import CrossEncoderReranker

        return CrossEncoderReranker(**kwargs)
    if name == "monot5":
        from citation_rag.rerank.monot5 import MonoT5Reranker

        return MonoT5Reranker(**kwargs)
    if name == "llm_listwise":
        from citation_rag.rerank.llm_listwise import ListwiseReranker

        return ListwiseReranker(**kwargs)
    raise ValueError(f"unknown reranker: {name!r}; expected one of {RERANKER_NAMES}")


def dry_run_matrix(index: str, search: str) -> list[dict[str, str]]:
    return [{"index": index, "search": search, "reranker": name} for name in RERANKER_NAMES]


def print_dry_run(index: str, search: str) -> None:
    matrix = dry_run_matrix(index, search)
    print(f"Wave 6 dry run: {len(matrix)} run(s)")
    for row in matrix:
        print(f"  index={row['index']} search={row['search']} reranker={row['reranker']}")


def run_rerank_experiment(
    index: str,
    search: str,
    results_dir: "str | Path | None" = None,
    reranker_kwargs: "dict[str, dict[str, Any]] | None" = None,
) -> list[dict[str, Any]]:
    """Runs the six rerankers on the dev split with oracle routing, through
    `citation_rag.evals.runner.run_eval`. Not exercised by this wave's tests
    (LIGHT/CPU only, at most 20 candidates): a real run needs a built index,
    the retriever (wave 5a), and, for `llm_listwise`, a live Ollama daemon
    (GPU class) -- none of which belong to this wave.
    """
    from citation_rag.evals.runner import run_eval
    from citation_rag.search.retriever import Retriever

    reranker_kwargs = reranker_kwargs or {}
    rows: list[dict[str, Any]] = []
    for name in RERANKER_NAMES:
        reranker = build_reranker(name, **reranker_kwargs.get(name, {}))
        retriever = Retriever(
            index_name=index,
            method=search,
            k=CANDIDATE_K,
            top=TOP_K,
            per_company_top=TOP_K,
            general_cap=TOP_K,
            reranker=reranker,
        )
        config = {"index": index, "search": search, "reranker": name, "k": TOP_K, "top": TOP_K}
        t0 = time.perf_counter()
        record = run_eval(config, "dev", retriever, results_dir=results_dir)
        wall_s = time.perf_counter() - t0
        rows.append(
            {
                "reranker": name,
                "run_id": record.run_id,
                "metrics": record.metrics,
                "wall_s": wall_s,
                "reranker_last_wall_ms": getattr(reranker, "last_wall_ms", None),
            }
        )
    return rows


def print_comparison(rows: list[dict[str, Any]], results_dir: "str | Path | None" = None) -> None:
    from citation_rag.evals.report import RESULTS_DIR, compare

    run_ids = [r["run_id"] for r in rows]
    print(compare(run_ids, results_dir=results_dir or RESULTS_DIR))


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.rerank.experiments")
    parser.add_argument("--exp", default="rerank", choices=["rerank"])
    parser.add_argument("--index", required=True)
    parser.add_argument("--search", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.dry_run:
        print_dry_run(args.index, args.search)
        return 0

    rows = run_rerank_experiment(args.index, args.search)
    print_comparison(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())

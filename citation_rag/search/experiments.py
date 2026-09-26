"""Wave 5 search experiments: `--exp A` (index x method comparison) and
`--exp hnsw` (exact-vs-HNSW recall and latency check).

Wave 5a hard rule: nothing in this file runs beyond `--dry-run` in this
wave (no corpus-scale runs; the 7 chunk indexes do not exist yet, they are
wave 4's GPU execution). `--dry-run` prints the run matrix and exits. The
real run paths (`run_experiment_a`, `run_experiment_hnsw`) are implemented
so later waves can use this file unchanged, once the indexes exist.

CLI:
    uv run python -m citation_rag.search.experiments --exp A --dry-run
    uv run python -m citation_rag.search.experiments --exp hnsw --dry-run
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from citation_rag.evals.golden import load_golden
from citation_rag.evals.runner import DEFAULT_GOLDEN_DIR, FIXTURE_GOLDEN, run_eval
from citation_rag.search import vector
from citation_rag.search.query_embed import embed_query
from citation_rag.search.retriever import Retriever

INDEXES = [
    "bge_small__s1",
    "bge_small__s2",
    "bge_small__s3",
    "bge_m3__s1",
    "bge_m3__s2",
    "bge_m3__s3",
    "bge_m3__s4",
]
METHODS = ["bm25", "vector", "hybrid"]
EF_SEARCH_VALUES = (40, 100, 200)


def matrix_a() -> list[dict[str, str]]:
    """The 7 indexes x 3 methods = 21 runs of experiment A."""
    return [{"index": idx, "search": method} for idx in INDEXES for method in METHODS]


def run_experiment_a(
    split: str = "dev",
    k: int = 50,
    top: int = 8,
    golden_path: Path | str | None = None,
    parsed_dir: Path | str | None = None,
    results_dir: Path | str | None = None,
) -> list[str]:
    """Run every (index, method) combination through `run_eval`, oracle routing.

    "Oracle routing" here means the retriever is handed each case's own
    `companies` directly, because `run_eval` calls
    `retriever(case.question, case.companies)`: no router call happens, so
    there is nothing to configure on the `Retriever` for this. Returns the
    run_ids, in matrix order, so callers can pass them to `evals.report`.
    """
    run_ids = []
    for combo in matrix_a():
        retriever = Retriever(index_name=combo["index"], method=combo["search"], k=k, top=top)
        config = {
            "index": combo["index"],
            "search": combo["search"],
            "reranker": "none",
            "k": k,
            "top": top,
            "router": False,
        }
        record = run_eval(
            config,
            split,
            retriever,
            golden_path=golden_path,
            parsed_dir=parsed_dir,
            results_dir=results_dir,
        )
        run_ids.append(record.run_id)
    return run_ids


def run_experiment_hnsw(
    index_name: str,
    split: str = "dev",
    ef_search_values: tuple[int, ...] = EF_SEARCH_VALUES,
    n_questions: int = 100,
    k: int = 50,
    golden_path: Path | str | None = None,
    schema: str | None = None,
) -> list[dict[str, Any]]:
    """Exact-vs-HNSW recall and latency, at a few `ef_search` settings.

    For each of up to `n_questions` dev questions: embed the question once,
    run an exact search and an HNSW search for the top `k`, and measure the
    overlap (HNSW recall against the exact top-k) and HNSW latency.
    """
    path = Path(golden_path) if golden_path else (DEFAULT_GOLDEN_DIR / f"{split}.jsonl")
    if not path.exists() or path.stat().st_size == 0:
        path = FIXTURE_GOLDEN
    cases = load_golden(path)[:n_questions]

    results = []
    for ef in ef_search_values:
        recalls = []
        latencies_ms = []
        for case in cases:
            qvec = embed_query(case.question)
            exact = vector.exact_search(index_name, qvec, k=k, schema=schema)
            exact_ids = {doc_id for doc_id, _ in exact}

            start = time.perf_counter()
            approx = vector.search(index_name, qvec, k=k, ef_search=ef, schema=schema)
            latencies_ms.append((time.perf_counter() - start) * 1000)

            approx_ids = {doc_id for doc_id, _ in approx}
            if exact_ids:
                recalls.append(len(exact_ids & approx_ids) / len(exact_ids))

        results.append(
            {
                "ef_search": ef,
                "recall": statistics.mean(recalls) if recalls else None,
                "latency_ms_p50": statistics.median(latencies_ms) if latencies_ms else None,
                "n_questions": len(cases),
            }
        )
    return results


def _print_dry_run_matrix_a() -> None:
    combos = matrix_a()
    print(f"Experiment A: {len(combos)} runs ({len(INDEXES)} indexes x {len(METHODS)} methods), oracle routing")
    for i, combo in enumerate(combos, start=1):
        print(f"  {i:2d}. index={combo['index']:<16} search={combo['search']}")


def _print_dry_run_hnsw(index_name: str) -> None:
    print(f"Experiment hnsw: index={index_name}, ef_search in {EF_SEARCH_VALUES}, 100 dev questions")
    for ef in EF_SEARCH_VALUES:
        print(f"  ef_search={ef}: exact top-50 vs HNSW top-50 recall + latency")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.search.experiments")
    parser.add_argument("--exp", required=True, choices=["A", "hnsw"])
    parser.add_argument("--index", default=INDEXES[0], help="index name for --exp hnsw")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--k", type=int, default=50)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the run matrix and exit; wave 5a allows only this mode",
    )
    args = parser.parse_args(argv)

    if not args.dry_run:
        print(
            "refusing: wave 5a only runs experiments.py with --dry-run (no corpus-scale runs yet)",
            file=sys.stderr,
        )
        return 1

    if args.exp == "A":
        _print_dry_run_matrix_a()
    else:
        _print_dry_run_hnsw(args.index)
    return 0


if __name__ == "__main__":
    sys.exit(main())

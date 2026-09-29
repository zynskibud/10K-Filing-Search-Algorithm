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

ALL_INDEXES = [
    "bge_small__s1",
    "bge_small__s2",
    "bge_small__s3",
    "bge_m3__s1",
    "bge_m3__s2",
    "bge_m3__s3",
    "bge_m3__s4",
]


def available_indexes() -> list[str]:
    """Indexes whose chunk table exists and has no null embeddings."""
    import psycopg

    from citation_rag.settings import Settings

    out = []
    with psycopg.connect(Settings().database_url) as conn, conn.cursor() as cur:
        for name in ALL_INDEXES:
            cur.execute("SELECT to_regclass(%s)", (f"chunks_{name}",))
            if cur.fetchone()[0] is None:
                continue
            cur.execute(f"SELECT count(*) FILTER (WHERE embedding IS NULL), count(*) FROM chunks_{name}")
            nulls, total = cur.fetchone()
            if total and nulls == 0:
                out.append(name)
    return out


INDEXES = list(ALL_INDEXES)  # narrowed to available_indexes() at run time
METHODS = ["bm25", "vector", "hybrid"]
EF_SEARCH_VALUES = (40, 100, 200)


def matrix_a(indexes: list[str] | None = None) -> list[dict[str, str]]:
    """indexes x 3 methods runs of experiment A (all 7 indexes by default)."""
    return [{"index": idx, "search": method} for idx in (indexes or INDEXES) for method in METHODS]


def run_experiment_a(
    split: str = "dev",
    k: int = 50,
    top: int = 8,
    golden_path: Path | str | None = None,
    parsed_dir: Path | str | None = None,
    results_dir: Path | str | None = None,
    indexes: list[str] | None = None,
) -> list[str]:
    """Run every (index, method) combination through `run_eval`, oracle routing.

    "Oracle routing" here means the retriever is handed each case's own
    `companies` directly, because `run_eval` calls
    `retriever(case.question, case.companies)`: no router call happens, so
    there is nothing to configure on the `Retriever` for this. Returns the
    run_ids, in matrix order, so callers can pass them to `evals.report`.
    """
    run_ids = []
    for combo in matrix_a(indexes):
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


def _print_dry_run_matrix_a(indexes: list[str] | None = None) -> None:
    combos = matrix_a(indexes)
    print(f"Experiment A: {len(combos)} runs ({len(indexes or INDEXES)} indexes x {len(METHODS)} methods), oracle routing")
    for i, combo in enumerate(combos, start=1):
        print(f"  {i:2d}. index={combo['index']:<16} search={combo['search']}")


def _print_dry_run_hnsw(index_name: str) -> None:
    print(f"Experiment hnsw: index={index_name}, ef_search in {EF_SEARCH_VALUES}, 100 dev questions")
    for ef in EF_SEARCH_VALUES:
        print(f"  ef_search={ef}: exact top-50 vs HNSW top-50 recall + latency")


def _write_winners(run_ids: list[str], results_dir: Path | str | None = None) -> dict:
    """Pick the winner by recall@8 (tie: MRR) and write runs/wave-5/winners.json
    plus a markdown table of every run."""
    import json

    from citation_rag.evals.report import RESULTS_DIR, compare

    rdir = Path(results_dir) if results_dir else RESULTS_DIR
    rows = []
    with open(rdir / "runs.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    by_id = {r["run_id"]: r for r in rows}
    runs = [by_id[i] for i in run_ids if i in by_id]

    def recall_key(r):
        # The harness names the headline metric recall@{k}; pick that key
        # (not the token-budget or per-type variants).
        return next(
            k for k in r["metrics"]
            if k.startswith("recall@") and "tok" not in k and "_" not in k
        )

    def key(r):
        m = r["metrics"]
        return (m[recall_key(r)]["value"], m.get("mrr", {}).get("value", 0))

    best = max(runs, key=key)
    rk = recall_key(best)
    winners = {"index": best["config"]["index"], "search": best["config"]["search"],
               "run_id": best["run_id"], "recall_metric": rk,
               "recall": best["metrics"][rk]["value"], "recall_ci95": best["metrics"][rk]["ci95"],
               "mrr": best["metrics"]["mrr"]["value"], "n_questions": best["n_questions"]}
    out_dir = Path("runs/wave-5"); out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "winners.json").write_text(json.dumps(winners, indent=2), encoding="utf-8")
    table = compare(run_ids, rdir)
    (out_dir / "experiment_A.md").write_text(table, encoding="utf-8")
    print(table)
    print(f"winner: {winners}")
    return winners


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.search.experiments")
    parser.add_argument("--exp", required=True, choices=["A", "hnsw"])
    parser.add_argument("--index", default=None, help="index name for --exp hnsw (default: the experiment A winner, else the first available)")
    parser.add_argument("--indexes", default=None, help="comma-separated index names (default: every index with vectors)")
    parser.add_argument("--split", default="dev")
    parser.add_argument("--k", type=int, default=50)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true", help="print the run matrix and exit")
    args = parser.parse_args(argv)

    if args.indexes:
        indexes = [x.strip() for x in args.indexes.split(",") if x.strip()]
    elif not args.dry_run:
        indexes = available_indexes()
        print(f"available indexes: {indexes}")
    else:
        indexes = list(INDEXES)

    if args.dry_run:
        if args.exp == "A":
            _print_dry_run_matrix_a(indexes)
        else:
            _print_dry_run_hnsw(args.index or indexes[0])
        return 0

    if args.exp == "A":
        run_ids = run_experiment_a(split=args.split, k=args.k, top=args.top, indexes=indexes)
        _write_winners(run_ids)
        return 0

    import json

    index_name = args.index
    if index_name is None:
        wpath = Path("runs/wave-5/winners.json")
        index_name = json.loads(wpath.read_text())["index"] if wpath.exists() else indexes[0]
    results = run_experiment_hnsw(index_name, split=args.split, k=args.k)
    out_dir = Path("runs/wave-5"); out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "hnsw_check.json").write_text(json.dumps({"index": index_name, "results": results}, indent=2), encoding="utf-8")
    for r in results:
        print(f"index={index_name} ef_search={r['ef_search']} recall_vs_exact={r['recall']:.3f} latency_ms_p50={r['latency_ms_p50']:.1f} n={r['n_questions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import json
import sys
import time
from pathlib import Path
from typing import Any

RERANKER_NAMES = ["none", "mmr", "colbert", "cross_encoder", "monot5", "llm_listwise"]

# Candidate pool size before reranking (the plan's "top 50"), and the final
# cut after reranking (the plan's "top 8").
CANDIDATE_K = 50
TOP_K = 8


def make_vector_lookup(index: str, schema: "str | None" = None, pool: Any = None):
    """`vector_lookup(chunk_ids) -> {chunk_id: vector}` against `chunks_{index}`.

    Uses the `citation_rag.search.vector` pool; `embedding::text` avoids
    needing a pgvector type adapter on the connection."""
    from citation_rag.search import vector

    table = vector.table_name(index, schema=schema)

    def lookup(chunk_ids):
        ids = list(chunk_ids)
        if not ids:
            return {}
        with (pool or vector.get_pool()).connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT id, embedding::text FROM {table} WHERE id = ANY(%s)", (ids,))
                rows = cur.fetchall()
            conn.commit()
        return {row[0]: json.loads(row[1]) for row in rows}

    return lookup


def build_reranker(
    name: str,
    index: "str | None" = None,
    *,
    schema: "str | None" = None,
    pool: Any = None,
    llm_client: Any = None,
    **kwargs: Any,
):
    """Lazy factory: only imports (and, for model rerankers, loads weights
    for) the reranker actually requested.

    `index` (for example `bge_small__s2`) is needed by `mmr`: it fetches chunk
    vectors from `chunks_{index}` and embeds the query with the model that
    built the index. `llm_listwise` gets a shared `OllamaClient` (thinking
    off, JSON format) unless `llm_client` or `client` is given."""
    if name == "none":
        from citation_rag.rerank.none import NoneReranker

        return NoneReranker()
    if name == "mmr":
        from citation_rag.rerank.mmr import MMRReranker

        if "vector_lookup" not in kwargs or "embed_query_fn" not in kwargs:
            if index is None:
                raise ValueError("build_reranker('mmr') needs the index name (for vectors and the query model)")
            from citation_rag.search.retriever import _embedder_for_index

            kwargs.setdefault("vector_lookup", make_vector_lookup(index, schema=schema, pool=pool))
            kwargs.setdefault("embed_query_fn", _embedder_for_index(index))
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

        if "client" not in kwargs:
            if llm_client is None:
                from citation_rag.llm import OllamaClient

                llm_client = OllamaClient(model="qwen3:8b", think=False, format="json")
            kwargs["client"] = llm_client
        return ListwiseReranker(**kwargs)
    raise ValueError(f"unknown reranker: {name!r}; expected one of {RERANKER_NAMES}")


def dry_run_matrix(index: str, search: str, names: "list[str] | None" = None) -> list[dict[str, str]]:
    return [{"index": index, "search": search, "reranker": name} for name in (names or RERANKER_NAMES)]


def print_dry_run(index: str, search: str, names: "list[str] | None" = None) -> None:
    matrix = dry_run_matrix(index, search, names)
    print(f"Wave 6 dry run: {len(matrix)} run(s)")
    for row in matrix:
        print(f"  index={row['index']} search={row['search']} reranker={row['reranker']}")


def run_rerank_experiment(
    index: str,
    search: str,
    results_dir: "str | Path | None" = None,
    reranker_kwargs: "dict[str, dict[str, Any]] | None" = None,
    names: "list[str] | None" = None,
    llm_client: Any = None,
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
    for name in (names or RERANKER_NAMES):
        try:
            reranker = build_reranker(name, index, llm_client=llm_client, **reranker_kwargs.get(name, {}))
        except Exception as exc:  # a missing model or daemon must not stop the other arms
            print(f"[rerank] {name}: skipped, {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            rows.append({"reranker": name, "run_id": None, "error": f"{type(exc).__name__}: {exc}"})
            continue
        retriever = Retriever(
            index_name=index,
            method=search,
            k=CANDIDATE_K,
            top=TOP_K,
            per_company_top=TOP_K,
            general_cap=TOP_K,
            reranker=reranker,
        )
        config = {"index": index, "search": search, "reranker": name, "k": CANDIDATE_K, "top": TOP_K}
        t0 = time.perf_counter()
        try:
            record = run_eval(config, "dev", retriever, results_dir=results_dir)
        except Exception as exc:
            print(f"[rerank] {name}: failed, {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            rows.append({"reranker": name, "run_id": None, "error": f"{type(exc).__name__}: {exc}"})
            continue
        wall_s = time.perf_counter() - t0
        print(f"[rerank] {name}: done in {wall_s:.0f}s", file=sys.stderr, flush=True)
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


def print_comparison(
    rows: list[dict[str, Any]],
    results_dir: "str | Path | None" = None,
    out_dir: "str | Path" = "runs/wave-6",
) -> None:
    from citation_rag.evals.report import RESULTS_DIR, compare

    run_ids = [r["run_id"] for r in rows if r.get("run_id")]
    table = compare(run_ids, results_dir=results_dir or RESULTS_DIR)
    print(table)
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rerank.md").write_text(table + "\n\nErrors: " + json.dumps([r for r in rows if r.get("error")]) + "\n", encoding="utf-8")
    (out_dir / "rows.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")


def _load_winners(path: "str | Path") -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.rerank.experiments")
    parser.add_argument("--exp", default="rerank", choices=["rerank"])
    parser.add_argument("--index", default=None, help="default: from --winners")
    parser.add_argument("--search", default=None, help="default: from --winners")
    parser.add_argument("--winners", default="runs/wave-5/winners.json", help="wave 5 winners file")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rerankers",
        default=None,
        help="comma-separated subset of " + ",".join(RERANKER_NAMES) + " (GPU arm: llm_listwise,mmr)",
    )
    parser.add_argument("--out-dir", default="runs/wave-6", help="where rerank.md and rows.json go")
    args = parser.parse_args(argv)
    names = [x.strip() for x in args.rerankers.split(",") if x.strip()] if args.rerankers else None
    bad = [n for n in (names or []) if n not in RERANKER_NAMES]
    if bad:
        parser.error(f"unknown reranker(s) {bad}; expected a subset of {RERANKER_NAMES}")

    index, search = args.index, args.search
    if index is None or search is None:
        winners = _load_winners(args.winners)
        index = index or winners["index"]
        search = search or winners["search"]

    if args.dry_run:
        print_dry_run(index, search, names)
        return 0

    rows = run_rerank_experiment(index, search, names=names)
    print_comparison(rows, out_dir=args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

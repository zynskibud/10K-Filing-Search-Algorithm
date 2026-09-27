"""Wave 4 execution driver (integration-1 item 7).

CLI:
    uv run python -m citation_rag.index.build_all --eta-only [--subset N] [--device mps]
    uv run python -m citation_rag.index.build_all --full [--device mps]

This is the command `scripts/waves.conf`'s wave-4 line runs, later, through
`scripts/run.sh 4`, after the coordinator's GO -- GPU class, corpus scale.
Nothing in this integration pass runs it beyond argument parsing and the
pure helpers (`select_subset`, `eta_gate`): no chunking, loading, or
embedding over the real corpus happens here (LIGHT, per the wave-4 and
integration-1 contracts).

Sequence:
  --eta-only:
    1. If `--subset N` is given: pick N filings at random (seed 20260925,
       deterministic) from `--parsed-dir`, write their accession numbers to
       `data/subset.txt`, and chunk only those. Otherwise chunk the whole
       corpus.
    2. Chunk all 7 (model, strategy) configs into `--chunks-dir` (default
       `data/chunks/`), via `citation_rag.chunk.run`.
    3. For each index, embed a deterministic 1% sample (no DB, no disk
       output -- `citation_rag.index.embed`'s own `--sample` path) and
       record tokens/second and the extrapolated ETA, in hours, for the
       full run. Every index's line is written to `runs/wave-4/eta.txt`.
    4. If any ETA exceeds `--max-hours` (default 8) and no `--subset` was
       given, exit nonzero (the coordinator reads `eta.txt` before allowing
       a `--full` run; `OPEN-QUESTIONS.md` records an ETA that runs long).
       Passing `--subset` is what "unless a subset is given" means: a
       deliberately smaller run is allowed to have a smaller ETA and skip
       the gate.
  --full:
    1. Chunk all 7 configs (same as above; deterministic, so re-chunking is
       always safe).
    2. Load filings/sections/tables and each index's chunks into Postgres
       (`citation_rag.index.load`). Each index's chunk table is truncated
       first, so re-running `--full` (or running it after an earlier
       `--eta-only`/`--full` attempt) never double-inserts -- see the
       "judgment calls" note in `reports/integration-1.md`.
    3. Embed every chunk for every index, bge_small indexes first (the
       order `INDEX_CONFIGS` is written in), via
       `citation_rag.index.embed.embed_chunks`, which shards, resumes, and
       calls `citation_rag.index.load.attach_vectors` (which also builds
       that index's HNSW index, right after its own data lands -- schema
       section 4's "create the index after the data is in").
    4. Build and save each index's BM25 pickle
       (`citation_rag.search.bm25.build_and_save_from_db`).

Judgment call (noted again in `reports/integration-1.md`): the contract's
prose lists "load filings and chunks" before the `--sample` ETA step, but
`citation_rag.index.embed`'s `--sample` path never touches Postgres or disk
by design, so `--eta-only` here skips the DB load entirely and only chunks
+ samples. The DB load only has to happen before `--full`'s real embedding
run, since `attach_vectors` `UPDATE`s rows that must already exist.
"""

from __future__ import annotations

import argparse
import random
import os
import sys
import time
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARSED_DIR = PROJECT_ROOT / "data" / "parsed"
DEFAULT_CHUNKS_DIR = PROJECT_ROOT / "data" / "chunks"
DEFAULT_SUBSET_PATH = PROJECT_ROOT / "data" / "subset.txt"
DEFAULT_ETA_PATH = PROJECT_ROOT / "runs" / "wave-4" / "eta.txt"

# (model, strategy) for the 7 indexes, bge_small first (the contract's "full
# runs, bge_small first"), in the order every step below processes them.
INDEX_CONFIGS: tuple[tuple[str, str], ...] = (
    ("bge_small", "s1"),
    ("bge_small", "s2"),
    ("bge_small", "s3"),
    ("bge_m3", "s1"),
    ("bge_m3", "s2"),
    ("bge_m3", "s3"),
    ("bge_m3", "s4"),
)

DEFAULT_TABLE_OPTION = 2
DEFAULT_MAX_HOURS = 8.0
DEFAULT_SAMPLE_FRACTION = 0.01
# Fixed seed for --subset, so the chosen filings (and everything downstream)
# are reproducible across runs.
SUBSET_SEED = 20260925


def index_names() -> list[str]:
    return [f"{model}__{strategy}" for model, strategy in INDEX_CONFIGS]


# --------------------------------------------------------------------------
# --subset: deterministic random subset of filings
# --------------------------------------------------------------------------


def select_subset(parsed_dir: "str | Path", n: int, seed: int = SUBSET_SEED) -> list[str]:
    """Accession numbers of a deterministic random N-filing subset of
    `parsed_dir` (every `*.json` there, regardless of `checks.passed` --
    the chunker itself skips failing filings), sorted for a stable file."""
    paths = sorted(Path(parsed_dir).glob("*.json"))
    accession_nos = [p.stem for p in paths]
    rng = random.Random(seed)
    n = min(n, len(accession_nos))
    chosen = rng.sample(accession_nos, n)
    chosen.sort()
    return chosen


def write_subset_list(accession_nos: Sequence[str], out_path: "str | Path" = DEFAULT_SUBSET_PATH) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{acc}\n" for acc in accession_nos)
    out_path.write_text(text, encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------
# chunk all 7 configs
# --------------------------------------------------------------------------


def chunk_all(
    parsed_dir: "str | Path",
    out_dir: "str | Path",
    filings: "list[str] | None" = None,
    table_option: int = DEFAULT_TABLE_OPTION,
) -> dict[str, Path]:
    """Chunk every (model, strategy) config, writing `{out_dir}/{index}.jsonl`.

    `filings`, when given (the `--subset` case), is the explicit, ordered
    list of parsed filing paths passed straight to
    `citation_rag.chunk.run.run`'s own `--filings` override.
    """
    from citation_rag.chunk import run as chunk_run

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for model, strategy in INDEX_CONFIGS:
        name = f"{model}__{strategy}"
        out_path = out_dir / f"{name}.jsonl"
        # Reuse: a full-corpus chunk file that already exists is not rebuilt
        # (a --subset run always rebuilds, because its content differs).
        if filings is None and out_path.exists() and out_path.stat().st_size > 0:
            print(f"[chunk] {name}: reusing {out_path}", file=sys.stderr, flush=True)
            written[name] = out_path
            continue
        # Strategies s1 to s3 do not depend on the embedding model (the
        # token ruler is always bge-small), so the two models' files are
        # byte-identical: link the twin instead of chunking again.
        twin = None
        for other_model in ("bge_small", "bge_m3"):
            if other_model != model and strategy in ("s1", "s2", "s3"):
                cand = out_dir / f"{other_model}__{strategy}.jsonl"
                if cand.exists() and cand.stat().st_size > 0 and cand in written.values():
                    twin = cand
        if twin is not None:
            if out_path.exists():
                out_path.unlink()
            os.link(twin, out_path)
            print(f"[chunk] {name}: linked to {twin.name}", file=sys.stderr, flush=True)
            written[name] = out_path
            continue
        chunk_run.run(strategy, table_option, str(out_path), str(parsed_dir), filings)
        written[name] = out_path
    return written


# --------------------------------------------------------------------------
# load filings/sections/tables and each index's chunks
# --------------------------------------------------------------------------


def load_all(parsed_dir: "str | Path", chunk_paths: dict[str, Path]) -> None:
    """Load filings/sections/tables once, then each index's chunks.

    Each chunk table is truncated before its `COPY` load, so running this
    (via `--full`) more than once -- for example after an earlier
    `--eta-only` attempt, or a retried `--full` -- never double-inserts.
    """
    import psycopg

    from citation_rag.index.load import load_chunks, load_filings
    from citation_rag.index.models import DIMENSIONS
    from citation_rag.index.schema import create_chunk_table, init_database
    from citation_rag.settings import Settings

    settings = Settings()
    with psycopg.connect(settings.database_url) as conn:
        init_database(conn)
        load_filings(parsed_dir, conn)
        for name, path in chunk_paths.items():
            model_name = name.split("__", 1)[0]
            create_chunk_table(conn, name, embedding_dim=DIMENSIONS[model_name])
            with conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE chunks_{name} RESTART IDENTITY")
            conn.commit()
            load_chunks(name, path, conn)


# --------------------------------------------------------------------------
# --eta-only: sample-embed each index, gate on --max-hours
# --------------------------------------------------------------------------


def run_eta(
    chunk_paths: dict[str, Path],
    device: str,
    sample_fraction: float = DEFAULT_SAMPLE_FRACTION,
) -> dict[str, dict]:
    """Sample-embed `sample_fraction` of each index (no DB, no disk output
    -- `citation_rag.index.embed`'s own `--sample` path) and return
    `{index: {"tokens_per_second": ..., "eta_hours": ...}}`."""
    from citation_rag.index import embed as embed_mod
    from citation_rag.index.models import load_embedder

    results: dict[str, dict] = {}
    embedders: dict[str, object] = {}
    for model_name, strategy in INDEX_CONFIGS:
        name = f"{model_name}__{strategy}"
        subset, n_chunks, full_tokens = embed_mod.sample_rows(chunk_paths[name], sample_fraction)
        if not subset:
            results[name] = {"tokens_per_second": 0.0, "eta_hours": 0.0, "n_chunks": 0}
            continue

        if model_name not in embedders:
            embedders[model_name] = load_embedder(model_name, device=device)
        embedder = embedders[model_name]
        subset = embed_mod.sort_by_length(subset)
        texts = [c["embed_text"] for c in subset]
        sample_tokens = sum(c.get("token_count", 0) for c in subset)

        start = time.perf_counter()
        embedder.encode_docs(texts)
        elapsed = time.perf_counter() - start

        tokens_per_second = sample_tokens / elapsed if elapsed > 0 else float("inf")
        eta_hours = (full_tokens / tokens_per_second) / 3600.0 if tokens_per_second > 0 else float("inf")
        results[name] = {
            "tokens_per_second": tokens_per_second,
            "eta_hours": eta_hours,
            "n_chunks": n_chunks,
        }
        print(
            f"[eta] {name}: {tokens_per_second:.0f} tok/s, {eta_hours:.2f} h for {n_chunks} chunks",
            file=sys.stderr,
            flush=True,
        )
    return results


def write_eta_report(results: dict[str, dict], device: str, out_path: "str | Path" = DEFAULT_ETA_PATH) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"index={name} device={device} chunks={r['n_chunks']} "
        f"tokens_per_second={r['tokens_per_second']:.1f} ETA_full_run_hours={r['eta_hours']:.2f}"
        for name, r in results.items()
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def eta_gate(results: dict[str, dict], max_hours: float, subset_given: bool) -> dict[str, float]:
    """Names of indexes whose ETA exceeds `max_hours`. The gate only stops
    the run (nonzero exit) when this is non-empty AND no `--subset` was
    given; the caller decides what to do with a non-empty dict either way."""
    return {name: r["eta_hours"] for name, r in results.items() if r["eta_hours"] > max_hours}


# --------------------------------------------------------------------------
# --full: embed everything, then BM25 pickles
# --------------------------------------------------------------------------


def run_full(chunk_paths: dict[str, Path], device: str) -> None:
    """Embed every chunk of every index (bge_small indexes first), writing
    resumable shards and attaching vectors (which also builds that index's
    HNSW index -- see `citation_rag.index.load.attach_vectors`)."""
    from citation_rag.index import embed as embed_mod

    for model_name, strategy in INDEX_CONFIGS:
        name = f"{model_name}__{strategy}"
        embed_mod.embed_file(name, chunk_paths[name], device=device)


def build_bm25_all(names: "list[str] | None" = None) -> None:
    import psycopg

    from citation_rag.search.bm25 import build_and_save_from_db
    from citation_rag.settings import Settings

    names = names if names is not None else index_names()
    settings = Settings()
    with psycopg.connect(settings.database_url) as conn:
        for name in names:
            build_and_save_from_db(name, conn)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: "Sequence[str] | None" = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Wave 4 execution driver: chunk, load, embed (sample or full), BM25."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--eta-only", action="store_true", help="Sample-embed each index and write the ETA report.")
    mode.add_argument("--full", action="store_true", help="Chunk, load, embed, and build BM25 for every index.")
    parser.add_argument("--device", default="mps", choices=["cpu", "mps"])
    parser.add_argument("--parsed-dir", default=str(DEFAULT_PARSED_DIR))
    parser.add_argument("--chunks-dir", default=str(DEFAULT_CHUNKS_DIR))
    parser.add_argument("--table-option", type=int, default=DEFAULT_TABLE_OPTION, choices=[0, 1, 2, 3])
    parser.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS)
    parser.add_argument(
        "--subset",
        type=int,
        default=None,
        metavar="N",
        help="Restrict to N random filings (seed 20260925); also bypasses the --max-hours gate.",
    )
    return parser.parse_args(argv)


def main(argv: "Sequence[str] | None" = None) -> int:
    args = parse_args(argv)

    filings = None
    if args.subset is not None:
        chosen = select_subset(args.parsed_dir, args.subset)
        subset_path = write_subset_list(chosen)
        filings = [str(Path(args.parsed_dir) / f"{acc}.json") for acc in chosen]
        print(f"subset: {len(chosen)} filings (seed={SUBSET_SEED}), written to {subset_path}")

    chunk_paths = chunk_all(args.parsed_dir, args.chunks_dir, filings=filings, table_option=args.table_option)

    if args.eta_only:
        results = run_eta(chunk_paths, device=args.device)
        eta_path = write_eta_report(results, device=args.device)
        for name, r in results.items():
            print(
                f"index={name} tokens_per_second={r['tokens_per_second']:.1f} "
                f"ETA_full_run_hours={r['eta_hours']:.2f}"
            )
        print(f"eta report: {eta_path}")

        over = eta_gate(results, args.max_hours, subset_given=args.subset is not None)
        if over:
            print(f"over --max-hours={args.max_hours}: {over}", file=sys.stderr)
            if args.subset is None:
                print("refusing: pass --subset N to run a smaller job, or raise --max-hours", file=sys.stderr)
                return 1
        return 0

    # --full
    load_all(args.parsed_dir, chunk_paths)
    run_full(chunk_paths, device=args.device)
    build_bm25_all(list(chunk_paths.keys()))
    print(f"full build complete for {len(chunk_paths)} indexes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

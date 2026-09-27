"""Embedding runner for wave 4c.

CLI:

    uv run python -m citation_rag.index.embed \\
        --index bge_small__s3 --chunks data/chunks/bge_small__s3.jsonl \\
        --device mps [--sample 0.01] [--full]

Exactly one of --sample / --full is required:
- --sample F: embeds a deterministic F-fraction sample of the chunks, prints
  tokens/second and the ETA for the full run, then exits. Nothing is written
  to disk or to Postgres.
- --full: embeds every chunk, writes resumable shards under
  data/vectors/{index}/, then loads the vectors into Postgres.

The model for an index is inferred from its name: "bge_small__*" or
"bge_m3__*". Batch size and the query/document encoding convention come from
citation_rag.index.models.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from citation_rag.index.models import Embedder, load_embedder

DEFAULT_SHARD_SIZE = 5000
# Fixed seed for the --sample subset, so the sample (and its printed ETA) is
# reproducible across runs rather than depending on wall-clock randomness.
SAMPLE_SEED = 42

AttachVectorsFn = Callable[[str, "Path | str", "Path | str"], None]


def model_name_for_index(index_name: str) -> str:
    """Infer the embedding model name ("bge_small" or "bge_m3") from an index name."""
    name = index_name.split("__", 1)[0]
    if name not in ("bge_small", "bge_m3"):
        raise ValueError(
            f"cannot infer model from index name: {index_name!r}; "
            "expected it to start with 'bge_small__' or 'bge_m3__'"
        )
    return name


def read_chunks(chunks_path: "Path | str") -> list[dict]:
    """Read one chunk record per line from a JSONL file."""
    chunks = []
    with open(chunks_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def sort_by_length(chunks: Sequence[dict]) -> list[dict]:
    """Sort chunks by token_count ascending, for efficient batching.

    A stable sort, so chunks with equal token_count keep their input order
    (this is what keeps shard contents deterministic across runs).
    """
    return sorted(chunks, key=lambda c: c.get("token_count", len(c.get("embed_text", ""))))


def sample_chunks(chunks: Sequence[dict], fraction: float, seed: int = SAMPLE_SEED) -> list[dict]:
    """Deterministic sample of a fraction of chunks (fixed seed, at least one row)."""
    if not 0 < fraction <= 1:
        raise ValueError(f"--sample must be in (0, 1], got {fraction!r}")
    n = max(1, round(len(chunks) * fraction))
    n = min(n, len(chunks))
    rng = random.Random(seed)
    return rng.sample(list(chunks), n)


def scan_chunks(chunks_path: "Path | str") -> tuple[list[int], list[int], list]:
    """One streaming pass over a chunk JSONL. Returns (byte_offsets, token_counts, ids)
    per row, so the file never has to be held in memory as Python objects.
    A 250,000-row file costs a few megabytes this way instead of gigabytes."""
    offsets: list[int] = []
    token_counts: list[int] = []
    ids: list = []
    with open(chunks_path, "rb") as f:
        pos = 0
        for raw in f:
            line = raw.strip()
            if line:
                row = json.loads(line)
                offsets.append(pos)
                token_counts.append(int(row.get("token_count") or len(row.get("embed_text", ""))))
                ids.append(row["id"])
            pos += len(raw)
    return offsets, token_counts, ids


def read_rows_at(chunks_path: "Path | str", offsets: Sequence[int], indices: Sequence[int]) -> list[dict]:
    """Read the rows at the given line indices by seeking to their byte offsets."""
    rows: list[dict] = []
    with open(chunks_path, "rb") as f:
        for i in indices:
            f.seek(offsets[i])
            rows.append(json.loads(f.readline()))
    return rows


def sample_rows(
    chunks_path: "Path | str", fraction: float, seed: int = SAMPLE_SEED
) -> tuple[list[dict], int, int]:
    """Deterministic streaming sample. Returns (rows, n_total, total_tokens)."""
    if not 0 < fraction <= 1:
        raise ValueError(f"--sample must be in (0, 1], got {fraction!r}")
    offsets, token_counts, _ = scan_chunks(chunks_path)
    n_total = len(offsets)
    if n_total == 0:
        return [], 0, 0
    k = min(n_total, max(1, round(n_total * fraction)))
    picked = sorted(random.Random(seed).sample(range(n_total), k))
    return read_rows_at(chunks_path, offsets, picked), n_total, sum(token_counts)


def _shard_paths(out_dir: Path, shard_idx: int) -> tuple[Path, Path]:
    return out_dir / f"shard_{shard_idx}.npy", out_dir / f"ids_{shard_idx}.npy"


def _shard_complete(vec_path: Path, ids_path: Path, expected_n: int) -> bool:
    """A shard is complete if both files exist and hold exactly expected_n rows."""
    if not (vec_path.exists() and ids_path.exists()):
        return False
    try:
        ids = np.load(ids_path)
        vectors = np.load(vec_path, mmap_mode="r")
    except Exception:
        return False
    return ids.shape[0] == expected_n and vectors.shape[0] == expected_n


def _attach_vectors_real(index_name: str, ids_path: "Path | str", vec_path: "Path | str") -> None:
    """Load vectors into Postgres via citation_rag.index.load.attach_vectors.

    Imported lazily, inside this function, so this module has no import-time
    dependency on load.py (owned by a different agent, written in parallel).
    """
    import psycopg

    from citation_rag.index.load import attach_vectors
    from citation_rag.settings import Settings

    settings = Settings()
    with psycopg.connect(settings.database_url) as conn:
        attach_vectors(index_name, ids_path, vec_path, conn)


def _embed_shards(
    index_name: str,
    n_rows: int,
    shard_rows,
    shard_size: int,
    out_dir: Path,
    embedder: Embedder,
    attach_vectors_fn: AttachVectorsFn | None,
) -> tuple[list[Path], list[Path], float, int]:
    """Shared shard loop. `shard_rows(shard_idx)` returns that shard's rows
    (already in global token-length order). A shard on disk with the right
    row count is skipped, which makes a run resumable."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n_shards = max(1, (n_rows + shard_size - 1) // shard_size) if n_rows else 0
    vec_paths: list[Path] = []
    ids_paths: list[Path] = []
    tokens_encoded = 0
    start = time.perf_counter()

    for shard_idx in range(n_shards):
        expected_n = min(shard_size, n_rows - shard_idx * shard_size)
        if expected_n <= 0:
            continue
        vec_path, ids_path = _shard_paths(out_dir, shard_idx)
        if _shard_complete(vec_path, ids_path, expected_n):
            vec_paths.append(vec_path)
            ids_paths.append(ids_path)
            continue
        shard = shard_rows(shard_idx)
        shard_ids = np.array([c["id"] for c in shard])
        texts = [c["embed_text"] for c in shard]
        vectors = embedder.encode_docs(texts)
        np.save(vec_path, vectors)
        np.save(ids_path, shard_ids)
        vec_paths.append(vec_path)
        ids_paths.append(ids_path)
        tokens_encoded += sum(c.get("token_count", 0) for c in shard)
        print(
            f"[{index_name}] shard {shard_idx + 1}/{n_shards} done, "
            f"{time.perf_counter() - start:.0f}s elapsed",
            file=sys.stderr,
            flush=True,
        )

    elapsed = time.perf_counter() - start
    attach_fn = attach_vectors_fn if attach_vectors_fn is not None else _attach_vectors_real
    for vec_path, ids_path in zip(vec_paths, ids_paths):
        attach_fn(index_name, ids_path, vec_path)
    return vec_paths, ids_paths, elapsed, tokens_encoded


def embed_chunks(
    index_name: str,
    chunks: Sequence[dict],
    device: str = "cpu",
    shard_size: int = DEFAULT_SHARD_SIZE,
    out_dir: "Path | str | None" = None,
    embedder: Embedder | None = None,
    attach_vectors_fn: AttachVectorsFn | None = None,
) -> tuple[list[Path], list[Path], float, int]:
    """Embed an in-memory list of chunks (tests and small inputs). Sorted by
    token length, sharded, resumable, then vectors attached. Returns
    (shard_vector_paths, shard_ids_paths, elapsed_seconds, tokens_encoded)."""
    model_name = model_name_for_index(index_name)
    if embedder is None:
        embedder = load_embedder(model_name, device=device)
    ordered = sort_by_length(chunks)
    out = Path(out_dir) if out_dir is not None else Path("data/vectors") / index_name

    def shard_rows(shard_idx: int) -> list[dict]:
        return ordered[shard_idx * shard_size : (shard_idx + 1) * shard_size]

    return _embed_shards(index_name, len(ordered), shard_rows, shard_size, out, embedder, attach_vectors_fn)


def embed_file(
    index_name: str,
    chunks_path: "Path | str",
    device: str = "cpu",
    shard_size: int = DEFAULT_SHARD_SIZE,
    out_dir: "Path | str | None" = None,
    embedder: Embedder | None = None,
    attach_vectors_fn: AttachVectorsFn | None = None,
) -> tuple[list[Path], list[Path], float, int, int]:
    """Embed a chunk JSONL file without loading it into memory: one scan for
    offsets and token counts, a global stable sort by token count, then each
    shard's rows are read by seeking. Returns the embed_chunks tuple plus n_rows."""
    model_name = model_name_for_index(index_name)
    if embedder is None:
        embedder = load_embedder(model_name, device=device)
    offsets, token_counts, _ = scan_chunks(chunks_path)
    n_rows = len(offsets)
    order = sorted(range(n_rows), key=lambda i: token_counts[i])  # stable
    out = Path(out_dir) if out_dir is not None else Path("data/vectors") / index_name

    def shard_rows(shard_idx: int) -> list[dict]:
        return read_rows_at(chunks_path, offsets, order[shard_idx * shard_size : (shard_idx + 1) * shard_size])

    vec_paths, ids_paths, elapsed, tokens_encoded = _embed_shards(
        index_name, n_rows, shard_rows, shard_size, out, embedder, attach_vectors_fn
    )
    return vec_paths, ids_paths, elapsed, tokens_encoded, n_rows


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Embed chunk JSONL rows into vector shards and load them into Postgres."
    )
    parser.add_argument("--index", required=True, help="Index name, e.g. bge_small__s3")
    parser.add_argument("--chunks", required=True, help="Path to the chunk JSONL file")
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps"])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--sample",
        type=float,
        default=None,
        metavar="FRACTION",
        help="Embed a deterministic FRACTION of the chunks and print an ETA, then exit.",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Embed every chunk, write shards, and load them into Postgres.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    model_name = model_name_for_index(args.index)

    if args.sample is not None:
        subset, n_total, full_tokens = sample_rows(args.chunks, args.sample)
        if not subset:
            print(f"No chunks found in {args.chunks}", file=sys.stderr)
            return 1
        embedder = load_embedder(model_name, device=args.device)
        subset = sort_by_length(subset)
        texts = [c["embed_text"] for c in subset]
        sample_tokens = sum(c.get("token_count", 0) for c in subset)

        start = time.perf_counter()
        embedder.encode_docs(texts)
        elapsed = time.perf_counter() - start

        tokens_per_second = sample_tokens / elapsed if elapsed > 0 else float("inf")
        eta_minutes = (
            (full_tokens / tokens_per_second) / 60.0 if tokens_per_second > 0 else float("inf")
        )
        print(f"index={args.index} device={args.device} sample_fraction={args.sample}")
        print(f"sample_chunks={len(subset)} sample_tokens={sample_tokens} elapsed_s={elapsed:.3f}")
        print(f"tokens_per_second={tokens_per_second:.1f}")
        print(f"full_chunks={n_total} full_tokens={full_tokens}")
        print(f"ETA_full_run_minutes={eta_minutes:.2f}")
        return 0

    # --full
    embedder = load_embedder(model_name, device=args.device)
    vec_paths, ids_paths, elapsed, tokens_encoded, n_rows = embed_file(
        args.index, args.chunks, device=args.device, embedder=embedder
    )
    if n_rows == 0:
        print(f"No chunks found in {args.chunks}", file=sys.stderr)
        return 1
    rate = tokens_encoded / elapsed if elapsed > 0 else float("inf")
    print(
        f"index={args.index} device={args.device} chunks={n_rows} "
        f"shards={len(vec_paths)} tokens_encoded={tokens_encoded} "
        f"elapsed_s={elapsed:.3f} tokens_per_second={rate:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

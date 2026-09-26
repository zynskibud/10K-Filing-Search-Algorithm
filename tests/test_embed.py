"""Tests for citation_rag.index.embed and citation_rag.index.models (wave 4c).

CPU only. Uses the 20-row fixture tests/fixtures/chunks_small.jsonl. Never
loads MPS. Never touches the real Postgres database: every call that would
attach vectors to a table is given a stub instead of the real
citation_rag.index.load.attach_vectors, so these tests do not depend on the
(separately owned) load.py.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from citation_rag.index import embed as embed_mod
from citation_rag.index.models import load_embedder

FIXTURE_CHUNKS = Path(__file__).parent / "fixtures" / "chunks_small.jsonl"


def read_fixture_chunks() -> list[dict]:
    chunks = []
    with FIXTURE_CHUNKS.open() as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def free_memory_gb() -> float:
    """Approximate free memory on macOS from `vm_stat`.

    Uses free + inactive + speculative pages, the usual approximation for
    memory the OS can hand out without swapping (inactive pages are clean
    and reclaimable).
    """
    try:
        out = subprocess.run(
            ["vm_stat"], capture_output=True, text=True, check=True, timeout=10
        ).stdout
    except Exception:
        return 0.0

    page_size = 4096
    m = re.search(r"page size of (\d+) bytes", out)
    if m:
        page_size = int(m.group(1))

    stats: dict[str, int] = {}
    for line in out.splitlines():
        m2 = re.match(r"Pages ([a-zA-Z ]+):\s+(\d+)\.", line)
        if m2:
            stats[m2.group(1).strip()] = int(m2.group(2))

    free_pages = stats.get("free", 0) + stats.get("inactive", 0) + stats.get("speculative", 0)
    return free_pages * page_size / (1024**3)


_FREE_MEM_GB = free_memory_gb()
_BGE_M3_SKIP_REASON = (
    f"only {_FREE_MEM_GB:.2f} GB free memory (vm_stat); bge-m3 load needs >= 4 GB free"
)


class StubAttach:
    """Records attach_vectors calls instead of touching Postgres."""

    def __init__(self):
        self.calls: list[tuple[str, Path, Path]] = []

    def __call__(self, index_name, ids_npy, vectors_npy):
        self.calls.append((index_name, Path(ids_npy), Path(vectors_npy)))


@pytest.fixture(scope="module")
def chunks():
    return read_fixture_chunks()


@pytest.fixture(scope="module")
def bge_small_embedder():
    return load_embedder("bge_small", device="cpu")


# --------------------------------------------------------------------------
# models.py: load_embedder / Embedder
# --------------------------------------------------------------------------


def test_bge_small_shapes_and_norm(chunks, bge_small_embedder):
    texts = [c["embed_text"] for c in chunks]
    vectors = bge_small_embedder.encode_docs(texts)
    assert vectors.shape == (len(chunks), 384)
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)


def test_bge_small_determinism(chunks, bge_small_embedder):
    texts = [c["embed_text"] for c in chunks]
    v1 = bge_small_embedder.encode_docs(texts)
    v2 = bge_small_embedder.encode_docs(texts)
    assert np.allclose(v1, v2, atol=1e-6)


def test_bge_small_query_instruction_changes_embedding(bge_small_embedder):
    text = "What was net income in fiscal 2025?"
    query_vec = bge_small_embedder.encode_query(text)
    doc_vec = bge_small_embedder.encode_docs([text])[0]
    assert query_vec.shape == (384,)
    assert abs(np.linalg.norm(query_vec) - 1.0) < 1e-3
    # The query gets the standard bge-small instruction prepended; the raw
    # document encoding does not, so the two vectors must differ.
    assert not np.allclose(query_vec, doc_vec, atol=1e-3)


@pytest.mark.skipif(_FREE_MEM_GB < 4.0, reason=_BGE_M3_SKIP_REASON)
def test_bge_m3_shapes_and_norm(chunks):
    embedder = load_embedder("bge_m3", device="cpu")
    texts = [c["embed_text"] for c in chunks]
    vectors = embedder.encode_docs(texts)
    assert vectors.shape == (len(chunks), 1024)
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)


# --------------------------------------------------------------------------
# embed.py: pure helpers
# --------------------------------------------------------------------------


def test_model_name_for_index():
    assert embed_mod.model_name_for_index("bge_small__s3") == "bge_small"
    assert embed_mod.model_name_for_index("bge_m3__s4") == "bge_m3"
    with pytest.raises(ValueError):
        embed_mod.model_name_for_index("unknown__s1")


def test_sort_by_length_is_ascending_and_stable(chunks):
    ordered = embed_mod.sort_by_length(chunks)
    counts = [c["token_count"] for c in ordered]
    assert counts == sorted(counts)
    assert {c["id"] for c in ordered} == {c["id"] for c in chunks}


def test_sample_chunks_deterministic_and_sized(chunks):
    s1 = embed_mod.sample_chunks(chunks, 0.5)
    s2 = embed_mod.sample_chunks(chunks, 0.5)
    assert len(s1) == 10
    assert [c["id"] for c in s1] == [c["id"] for c in s2]

    tiny = embed_mod.sample_chunks(chunks, 0.01)
    assert len(tiny) == 1


# --------------------------------------------------------------------------
# embed.py: embed_chunks (shards, resumability, attach_vectors stubbing)
# --------------------------------------------------------------------------


def test_embed_chunks_single_shard_calls_stub_once(chunks, bge_small_embedder, tmp_path):
    stub = StubAttach()
    vec_paths, ids_paths, elapsed, tokens_encoded = embed_mod.embed_chunks(
        "bge_small__s2",
        chunks,
        device="cpu",
        out_dir=tmp_path / "vectors",
        embedder=bge_small_embedder,
        attach_vectors_fn=stub,
    )
    assert len(vec_paths) == 1  # 20 chunks < default shard_size (5000)
    assert len(stub.calls) == 1
    assert stub.calls[0][0] == "bge_small__s2"
    assert tokens_encoded == sum(c["token_count"] for c in chunks)
    assert elapsed >= 0

    vectors = np.load(vec_paths[0])
    ids = np.load(ids_paths[0])
    assert vectors.shape == (20, 384)
    assert set(ids.tolist()) == {c["id"] for c in chunks}


def test_embed_chunks_resumability(chunks, bge_small_embedder, tmp_path):
    stub = StubAttach()
    out_dir = tmp_path / "vectors"

    vec_paths1, ids_paths1, _, tokens1 = embed_mod.embed_chunks(
        "bge_small__s3",
        chunks,
        device="cpu",
        shard_size=5,
        out_dir=out_dir,
        embedder=bge_small_embedder,
        attach_vectors_fn=stub,
    )
    assert len(vec_paths1) == 4  # 20 chunks / shard_size 5
    assert tokens1 == sum(c["token_count"] for c in chunks)
    mtimes_before = {p: p.stat().st_mtime_ns for p in vec_paths1}

    # Delete one shard's files; rerun should rebuild only that shard.
    rebuilt_idx = 1
    vec_paths1[rebuilt_idx].unlink()
    ids_paths1[rebuilt_idx].unlink()

    vec_paths2, ids_paths2, _, tokens2 = embed_mod.embed_chunks(
        "bge_small__s3",
        chunks,
        device="cpu",
        shard_size=5,
        out_dir=out_dir,
        embedder=bge_small_embedder,
        attach_vectors_fn=stub,
    )
    assert vec_paths2 == vec_paths1
    for i, p in enumerate(vec_paths2):
        if i == rebuilt_idx:
            assert p.stat().st_mtime_ns > mtimes_before[p]
        else:
            assert p.stat().st_mtime_ns == mtimes_before[p]

    # Only the rebuilt shard's tokens were re-encoded (timed) on the second run.
    rebuilt_shard = embed_mod.sort_by_length(chunks)[
        rebuilt_idx * 5 : (rebuilt_idx + 1) * 5
    ]
    assert tokens2 == sum(c["token_count"] for c in rebuilt_shard)

    # attach_vectors_fn is called once per shard, on every run.
    assert len(stub.calls) == 8


# --------------------------------------------------------------------------
# CLI: parse_args, --sample ETA line, --full end to end (stubbed attach)
# --------------------------------------------------------------------------


def test_cli_requires_sample_or_full():
    with pytest.raises(SystemExit):
        embed_mod.parse_args(
            ["--index", "bge_small__s1", "--chunks", str(FIXTURE_CHUNKS), "--device", "cpu"]
        )


def test_cli_rejects_both_sample_and_full():
    with pytest.raises(SystemExit):
        embed_mod.parse_args(
            [
                "--index", "bge_small__s1",
                "--chunks", str(FIXTURE_CHUNKS),
                "--device", "cpu",
                "--sample", "0.5",
                "--full",
            ]
        )


def test_cli_sample_prints_eta(monkeypatch, bge_small_embedder, capsys):
    # Reuse the module-scoped embedder instead of loading the model again.
    monkeypatch.setattr(
        embed_mod, "load_embedder", lambda name, device="cpu": bge_small_embedder
    )

    exit_code = embed_mod.main(
        [
            "--index", "bge_small__s1",
            "--chunks", str(FIXTURE_CHUNKS),
            "--device", "cpu",
            "--sample", "0.5",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert re.search(r"ETA_full_run_minutes=[\d.]+", captured.out)
    assert re.search(r"tokens_per_second=[\d.]+", captured.out)


def test_cli_full_writes_shards_and_calls_attach(monkeypatch, bge_small_embedder, tmp_path, capsys):
    monkeypatch.setattr(
        embed_mod, "load_embedder", lambda name, device="cpu": bge_small_embedder
    )
    calls = []

    def stub(index_name, ids_path, vec_path):
        calls.append((index_name, Path(ids_path), Path(vec_path)))

    monkeypatch.setattr(embed_mod, "_attach_vectors_real", stub)
    monkeypatch.chdir(tmp_path)

    exit_code = embed_mod.main(
        [
            "--index", "bge_small__s1",
            "--chunks", str(FIXTURE_CHUNKS),
            "--device", "cpu",
            "--full",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert (tmp_path / "data" / "vectors" / "bge_small__s1" / "shard_0.npy").exists()
    assert (tmp_path / "data" / "vectors" / "bge_small__s1" / "ids_0.npy").exists()
    assert len(calls) == 1
    assert calls[0][0] == "bge_small__s1"
    assert "tokens_per_second=" in captured.out

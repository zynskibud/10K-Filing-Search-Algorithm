"""Tests for citation_rag.rerank (wave 6): interface, MMR, cross-encoder,
monoT5, ColBERT, listwise LLM reranking, timing, and the experiment dry run.

LIGHT/CPU only: at most 20 candidates (tests/fixtures/rerank_candidates.jsonl),
short texts, no MPS, no Ollama calls (FakeLLMClient stands in for the LLM
reranker). ColBERTv2 and monoT5 were downloaded into the project's offline
HF cache by this wave (not cached before); if either model fails to load,
its tests are skipped with the reason (the only allowed skip, per the
contract).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from citation_rag.evals.runner import Result
from citation_rag.rerank.base import RerankedResult
from citation_rag.rerank.colbert import ColbertReranker
from citation_rag.rerank.cross_encoder import CrossEncoderReranker
from citation_rag.rerank.llm_listwise import ListwiseReranker
from citation_rag.rerank.mmr import MMRReranker, mmr_select
from citation_rag.rerank.monot5 import MonoT5Reranker
from citation_rag.rerank.none import NoneReranker
from citation_rag.search.router import FakeLLMClient

FIXTURE = Path(__file__).parent / "fixtures" / "rerank_candidates.jsonl"

QUESTION = "What caused the decline in Fixture Corp's gross margin in fiscal 2025?"

TOY_QUESTION = "What is the capital of France?"
TOY_CANDIDATES = [
    Result("t1", "Paris is the capital and most populous city of France.", 12, None, None, 1, 1, "toy"),
    Result("t2", "Berlin is the capital of Germany.", 7, None, None, 1, 1, "toy"),
    Result("t3", "The Eiffel Tower was completed in 1889.", 8, None, None, 1, 1, "toy"),
    Result("t4", "Bananas are a good source of potassium.", 8, None, None, 1, 1, "toy"),
    Result("t5", "Rome is the capital of Italy.", 6, None, None, 1, 1, "toy"),
    Result("t6", "The stock market closed lower on Friday.", 8, None, None, 1, 1, "toy"),
]


def load_candidates() -> list[Result]:
    rows = []
    with FIXTURE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            rows.append(
                Result(
                    chunk_id=d["chunk_id"],
                    text=d["text"],
                    token_count=d["token_count"],
                    section_id=d["section_id"],
                    table_id=d["table_id"],
                    page_start=d["page_start"],
                    page_end=d["page_end"],
                    accession_no=d["accession_no"],
                )
            )
    assert len(rows) == 20
    return rows


def _load_or_skip(factory):
    """Build a model reranker and force its weights to load, or skip the
    test with the failure reason. The only skip the contract allows."""
    reranker = factory()
    try:
        reranker.rerank("warm up", load_candidates()[:1], top=1)
    except Exception as exc:  # pragma: no cover - only hit on a broken cache
        pytest.skip(f"model load failed: {exc}")
    return reranker


@pytest.fixture(scope="module")
def cross_encoder_reranker():
    return _load_or_skip(CrossEncoderReranker)


@pytest.fixture(scope="module")
def monot5_reranker():
    return _load_or_skip(MonoT5Reranker)


@pytest.fixture(scope="module")
def colbert_reranker():
    return _load_or_skip(ColbertReranker)


def _assert_interface(results, candidates, top):
    assert len(results) == top
    candidate_ids = {c.chunk_id for c in candidates}
    for r in results:
        assert isinstance(r, RerankedResult)
        assert r.chunk_id in candidate_ids
        assert isinstance(r.rerank_score, float)


# ---------------------------------------------------------------------------
# Interface: every reranker returns exactly `top`, all from the candidates,
# each carrying `rerank_score`.
# ---------------------------------------------------------------------------


def test_none_interface_and_identity_order():
    candidates = load_candidates()
    reranker = NoneReranker()
    out = reranker.rerank(QUESTION, candidates, top=8)
    _assert_interface(out, candidates, 8)
    assert [r.chunk_id for r in out] == [c.chunk_id for c in candidates[:8]]


def test_mmr_interface():
    candidates = load_candidates()
    vectors = {
        c.chunk_id: [float(i % 7), float((i * 3) % 5), float((i * 5) % 11)]
        for i, c in enumerate(candidates)
    }
    reranker = MMRReranker(
        vector_lookup=lambda ids: {i: vectors[i] for i in ids},
        embed_query_fn=lambda q: [1.0, 0.0, 0.0],
    )
    out = reranker.rerank(QUESTION, candidates, top=8)
    _assert_interface(out, candidates, 8)


def test_cross_encoder_interface(cross_encoder_reranker):
    candidates = load_candidates()
    out = cross_encoder_reranker.rerank(QUESTION, candidates, top=8)
    _assert_interface(out, candidates, 8)


def test_monot5_interface(monot5_reranker):
    candidates = load_candidates()
    out = monot5_reranker.rerank(QUESTION, candidates, top=8)
    _assert_interface(out, candidates, 8)


def test_colbert_interface(colbert_reranker):
    candidates = load_candidates()
    out = colbert_reranker.rerank(QUESTION, candidates, top=8)
    _assert_interface(out, candidates, 8)


def test_llm_listwise_interface():
    candidates = load_candidates()
    order_json = json.dumps({"order": list(range(1, len(candidates) + 1))})
    reranker = ListwiseReranker(client=FakeLLMClient(order_json))
    out = reranker.rerank(QUESTION, candidates, top=8)
    _assert_interface(out, candidates, 8)


# ---------------------------------------------------------------------------
# MMR: a near-duplicate is pushed below a less similar but relevant one.
# ---------------------------------------------------------------------------


def test_mmr_select_pushes_near_duplicate_below_diverse_relevant():
    q = [1.0, 0.0, 0.0]
    a = [0.9, 0.4359, 0.0]  # closest to the query
    b = [0.85, 0.5268, 0.0]  # near-duplicate of a; second-highest raw similarity to q
    c = [0.6, -0.8, 0.0]  # less similar to q than b, but distinct from a

    order = mmr_select(q, [a, b, c], lambda_=0.7, top=3)
    assert order == [0, 2, 1]  # a, then c, with duplicate b pushed last


def test_mmr_reranker_pushes_near_duplicate_below_diverse_relevant():
    candidates = [
        Result("A", "first duplicate passage about the topic", 6, None, None, 1, 1, "acc"),
        Result("B", "near duplicate passage, almost identical to A", 6, None, None, 1, 1, "acc"),
        Result("C", "a different but still relevant passage", 6, None, None, 1, 1, "acc"),
    ]
    vectors = {"A": [0.9, 0.4359, 0.0], "B": [0.85, 0.5268, 0.0], "C": [0.6, -0.8, 0.0]}
    reranker = MMRReranker(
        vector_lookup=lambda ids: {i: vectors[i] for i in ids},
        embed_query_fn=lambda q: [1.0, 0.0, 0.0],
    )
    out = reranker.rerank(QUESTION, candidates, top=3)
    assert [r.chunk_id for r in out] == ["A", "C", "B"]


# ---------------------------------------------------------------------------
# Cross-encoder / monoT5: the candidate that answers a toy question ranks
# first, on 6 short candidates.
# ---------------------------------------------------------------------------


def test_cross_encoder_ranks_relevant_candidate_first(cross_encoder_reranker):
    out = cross_encoder_reranker.rerank(TOY_QUESTION, TOY_CANDIDATES, top=6)
    assert out[0].chunk_id == "t1"


def test_monot5_ranks_relevant_candidate_first(monot5_reranker):
    out = monot5_reranker.rerank(TOY_QUESTION, TOY_CANDIDATES, top=6)
    assert out[0].chunk_id == "t1"


# ---------------------------------------------------------------------------
# ColBERT: MaxSim of a query with itself is higher than with an unrelated
# text.
# ---------------------------------------------------------------------------


def test_colbert_maxsim_self_higher_than_unrelated(colbert_reranker):
    query = "gross margin decline due to higher input costs"
    same = Result("same", query, 6, None, None, 1, 1, "toy")
    unrelated = Result(
        "unrelated", "The cat sat on the windowsill in the afternoon sun.", 10, None, None, 1, 1, "toy"
    )
    out = colbert_reranker.rerank(query, [unrelated, same], top=2)
    assert out[0].chunk_id == "same"
    assert out[0].rerank_score > out[1].rerank_score


# ---------------------------------------------------------------------------
# Listwise LLM: the fake client's ordering is applied; a malformed response,
# and a partial one, fall back to fusion order.
# ---------------------------------------------------------------------------


def test_listwise_fake_client_order_applied():
    candidates = load_candidates()[:5]
    reverse_order = list(range(len(candidates), 0, -1))
    client = FakeLLMClient(json.dumps({"order": reverse_order}))
    reranker = ListwiseReranker(client=client)
    out = reranker.rerank(QUESTION, candidates, top=5)
    assert [r.chunk_id for r in out] == [c.chunk_id for c in reversed(candidates)]


def test_listwise_malformed_response_falls_back_to_fusion_order():
    candidates = load_candidates()[:5]
    client = FakeLLMClient("not json at all")
    reranker = ListwiseReranker(client=client)
    out = reranker.rerank(QUESTION, candidates, top=5)
    assert [r.chunk_id for r in out] == [c.chunk_id for c in candidates]


def test_listwise_partial_response_fills_missing_in_fusion_order():
    candidates = load_candidates()[:5]
    ids = [c.chunk_id for c in candidates]
    # Only candidates 3 and 1 are named; 2, 4, 5 are missing and should be
    # appended, in fusion order, after them.
    client = FakeLLMClient(json.dumps({"order": [3, 1]}))
    reranker = ListwiseReranker(client=client)
    out = reranker.rerank(QUESTION, candidates, top=5)
    assert [r.chunk_id for r in out] == [ids[2], ids[0], ids[1], ids[3], ids[4]]


# ---------------------------------------------------------------------------
# Timing: every reranker records its wall time for the call.
# ---------------------------------------------------------------------------


def test_timing_recorded_for_none_and_mmr_and_listwise():
    candidates = load_candidates()

    none_reranker = NoneReranker()
    none_reranker.rerank(QUESTION, candidates, top=8)
    assert isinstance(none_reranker.last_wall_ms, float) and none_reranker.last_wall_ms >= 0.0

    mmr_reranker = MMRReranker(
        vector_lookup=lambda ids: {i: [1.0, 0.0] for i in ids},
        embed_query_fn=lambda q: [1.0, 0.0],
    )
    mmr_reranker.rerank(QUESTION, candidates, top=8)
    assert isinstance(mmr_reranker.last_wall_ms, float) and mmr_reranker.last_wall_ms >= 0.0

    listwise = ListwiseReranker(client=FakeLLMClient(json.dumps({"order": list(range(1, 21))})))
    listwise.rerank(QUESTION, candidates, top=8)
    assert isinstance(listwise.last_wall_ms, float) and listwise.last_wall_ms >= 0.0


def test_timing_recorded_for_model_rerankers(cross_encoder_reranker, monot5_reranker, colbert_reranker):
    candidates = load_candidates()
    for reranker in (cross_encoder_reranker, monot5_reranker, colbert_reranker):
        reranker.rerank(QUESTION, candidates, top=8)
        assert isinstance(reranker.last_wall_ms, float)
        assert reranker.last_wall_ms >= 0.0


# ---------------------------------------------------------------------------
# Experiment dry run.
# ---------------------------------------------------------------------------


def test_experiments_dry_run_prints_six_run_matrix(capsys):
    from citation_rag.rerank.experiments import RERANKER_NAMES, main

    rc = main(["--exp", "rerank", "--index", "bge_small", "--search", "hybrid", "--dry-run"])
    captured = capsys.readouterr()
    assert rc == 0
    assert len(RERANKER_NAMES) == 6
    for name in RERANKER_NAMES:
        assert name in captured.out

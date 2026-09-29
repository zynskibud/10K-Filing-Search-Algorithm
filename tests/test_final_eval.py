"""Tests for wave 8c: citation_rag/evals/final.py (the sealed test-set run).

Everything is fake: fake systems, a fake judge, a throwaway results folder,
and a small golden file. No Ollama, no database, no models.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from citation_rag.evals import final
from citation_rag.evals.judge import FakeJudge
from citation_rag.evals.runner import Result

GOLDEN_ROWS = [
    {
        "id": f"t{i}",
        "question": f"Question {i}?",
        "type": "fact_lookup",
        "companies": ["0000000001"],
        "accession_no": "0000000001-25-000001",
        "item": "1",
        "evidence": "we hold cash of $5 million",
        "page": 1,
        "answer": "$5 million",
        "answer_kind": "text",
    }
    for i in range(3)
]


@pytest.fixture()
def golden(tmp_path: Path) -> Path:
    path = tmp_path / "test.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in GOLDEN_ROWS) + "\n", encoding="utf-8")
    return path


def _judge() -> FakeJudge:
    def respond(prompt: str) -> str:
        if prompt.startswith("You split a model's answer"):
            return json.dumps({"reason": "r", "claims": ["Cash was $5 million."]})
        if prompt.startswith("You check whether a single claim"):
            return json.dumps({"reason": "r", "label": "supported"})
        if prompt.startswith("You check whether a cited chunk"):
            return json.dumps({"reason": "r", "label": "supports"})
        if prompt.startswith("You check whether a model answered or refused"):
            return json.dumps({"reason": "r", "label": "answered"})
        return json.dumps({"reason": "r", "label": "correct"})

    return FakeJudge(respond)


def _fake_retriever(question: str, companies) -> list[Result]:
    return [Result("c1", "Acme said we hold cash of $5 million today.", 10, "s1", None, 1, 1, "0000000001-25-000001")]


def _rag_answer(question: str, companies) -> dict:
    return {
        "answer": "Cash was $5 million [1].",
        "citations": [{"ref": 1, "quote": "cash of $5 million"}],
        "answerable": True,
        "blocks": [{"ref": 1, "text": "we hold cash of $5 million"}],
    }


def _systems() -> list[final.System]:
    calls = {"n": 0}

    def tokens() -> dict:
        calls["n"] += 1
        return {"input_tokens": 100, "output_tokens": 10}

    return [
        final.System("final_rag", _rag_answer, _fake_retriever, config={"k": 8, "top": 8}),
        final.System(
            "run_A",
            lambda q, c: {"answer": "I do not know.", "citations": [], "answerable": False},
            None,
            config={"mode": "no_docs"},
            tokens_fn=tokens,
        ),
    ]


def test_run_final_runs_every_system_and_writes_marker(tmp_path: Path, golden: Path) -> None:
    results = tmp_path / "results"
    summary = final.run_final(unseal=True, systems=_systems(), judge_client=_judge(), golden_path=golden, results_dir=results)

    assert summary["n_questions"] == 3
    assert set(summary["systems"]) == {"final_rag", "run_A"}
    rag_row = summary["systems"]["final_rag"]
    assert rag_row["retrieval"]["metrics"]["recall@8"]["value"] == 1.0
    assert rag_row["judged"]["n"] == 3 and rag_row["judged"]["correct_share"] == 1.0
    assert rag_row["answers"]["n_answered"] == 3
    a_row = summary["systems"]["run_A"]
    assert "retrieval" not in a_row  # run A has no retriever
    assert a_row["answers"]["cost"] == {"api_usd": 0.0, "wall_s": pytest.approx(a_row["answers"]["cost"]["wall_s"]), "input_tokens": 300, "output_tokens": 30}
    assert (results / "final" / "summary.md").read_text().count("\n") >= 4
    judged_rows = (results / "final" / "final_rag.judged.jsonl").read_text().splitlines()
    assert len(judged_rows) == 3
    assert json.loads((results / final.MARKER_NAME).read_text())["status"] == "done"


def test_second_run_is_refused_and_changes_nothing(tmp_path: Path, golden: Path) -> None:
    results = tmp_path / "results"
    final.run_final(unseal=True, systems=_systems(), judge_client=_judge(), golden_path=golden, results_dir=results)
    before = sorted(p.name for p in results.rglob("*") if p.is_file())
    marker_text = (results / final.MARKER_NAME).read_text()

    asked = []
    spy = final.System("spy", lambda q, c: asked.append(q) or {"answer": ""}, None)
    with pytest.raises(final.SealedError, match="already used"):
        final.run_final(unseal=True, systems=[spy], judge_client=_judge(), golden_path=golden, results_dir=results)

    assert asked == []
    assert sorted(p.name for p in results.rglob("*") if p.is_file()) == before
    assert (results / final.MARKER_NAME).read_text() == marker_text


def test_unseal_flag_is_required(tmp_path: Path, golden: Path) -> None:
    with pytest.raises(final.SealedError, match="--unseal"):
        final.run_final(unseal=False, systems=_systems(), judge_client=_judge(), golden_path=golden, results_dir=tmp_path / "r")
    assert not (tmp_path / "r" / final.MARKER_NAME).exists()


def test_marker_is_written_before_questions_run(tmp_path: Path, golden: Path) -> None:
    results = tmp_path / "results"
    seen = {}

    def answer(q, c):
        seen["marker"] = (results / final.MARKER_NAME).exists()
        return {"answer": "x", "citations": [], "answerable": True}

    final.run_final(unseal=True, systems=[final.System("s", answer, None)], judge_client=_judge(), golden_path=golden, results_dir=results)
    assert seen["marker"] is True


def test_one_failing_system_does_not_lose_the_others(tmp_path: Path, golden: Path) -> None:
    def boom(q, c):
        raise RuntimeError("model down")

    systems = [final.System("bad", boom, None), final.System("final_rag", _rag_answer, _fake_retriever, config={"k": 8})]
    summary = final.run_final(unseal=True, systems=systems, judge_client=_judge(), golden_path=golden, results_dir=tmp_path / "r")
    assert "model down" in summary["systems"]["bad"]["error"]
    assert "error" not in summary["systems"]["final_rag"]
    assert "error: RuntimeError" in final.summary_markdown(summary)


def test_cli_refuses_without_unseal_and_when_marker_exists(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(final, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(final, "build_default_systems", lambda: pytest.fail("systems must not be built"))

    assert final.main([]) == 2
    assert "sealed" in capsys.readouterr().err

    (tmp_path / final.MARKER_NAME).write_text("{}")
    assert final.main(["--unseal"]) == 1
    assert "already used" in capsys.readouterr().err

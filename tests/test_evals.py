"""Unit tests for the eval harness (wave 3b)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from citation_rag.evals import calibration, judge, report, router_metrics
from citation_rag.evals.golden import GoldenCase, Problem, load_golden, validate
from citation_rag.evals.judge import FakeJudge, JudgeParseError
from citation_rag.evals.retrieval_metrics import (
    bootstrap_ci,
    bootstrap_diff_ci,
    is_hit,
    mrr,
    recall_at_k,
    recall_at_token_budget,
    split_by_table,
    split_by_type,
)
from citation_rag.evals.runner import make_random_retriever, run_eval

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_GOLDEN = FIXTURES_DIR / "golden_dev.jsonl"
FIXTURE_PARSED_DIR = FIXTURES_DIR / "parsed"


class FakeResult(NamedTuple):
    text: str


# --------------------------------------------------------------------------
# is_hit
# --------------------------------------------------------------------------


def test_is_hit_exact_match():
    evidence = "Total revenue | 2025: 13,845 | 2024: 11,800"
    chunk = "Revenue by segment (in thousands)\n" + evidence + "\nsome other line"
    assert is_hit(chunk, evidence) is True


def test_is_hit_whitespace_normalized_exact_match():
    evidence = "The company depends on a single supplier."
    chunk = "The   company depends\non a single   supplier.   "
    assert is_hit(chunk, evidence) is True


def test_is_hit_80_percent_partial_match():
    evidence = "A small number of customers account for a large share of our revenue and results."
    cutoff = int(len(evidence) * 0.85)
    chunk = evidence[:cutoff] + " ...cut off here, unrelated trailing text follows."
    assert is_hit(chunk, evidence) is True


def test_is_hit_below_80_percent_is_not_a_hit():
    evidence = "A small number of customers account for a large share of our revenue and results."
    cutoff = int(len(evidence) * 0.5)
    chunk = evidence[:cutoff] + " completely unrelated text with no further overlap at all."
    assert is_hit(chunk, evidence) is False


def test_is_hit_split_across_two_chunks_fails_both():
    evidence = "Our products depend on components sourced from a single overseas supplier, and any disruption would delay shipments."
    half = len(evidence) // 2
    chunk_a = "leading filler text. " + evidence[:half]
    chunk_b = evidence[half:] + " trailing filler text."
    assert is_hit(chunk_a, evidence) is False
    assert is_hit(chunk_b, evidence) is False


def test_is_hit_table_line():
    evidence = "Product | 2025: 12,345 | 2024: 10,000"
    table_text = "Revenue by segment (in thousands)\nProduct | 2025: 12,345 | 2024: 10,000\nServices | 2025: 1,500 | 2024: 1,800"
    assert is_hit(table_text, evidence) is True


# --------------------------------------------------------------------------
# retrieval metric math
# --------------------------------------------------------------------------


def test_recall_at_k_and_mrr():
    evidence = "the exact evidence text"
    results = [
        FakeResult("no match here"),
        FakeResult("prefix " + evidence + " suffix"),
        FakeResult("something else"),
    ]
    assert recall_at_k(results, evidence, k=1) is False
    assert recall_at_k(results, evidence, k=2) is True
    assert mrr(results, evidence) == pytest.approx(0.5)


def test_mrr_no_hit_is_zero():
    results = [FakeResult("a"), FakeResult("b")]
    assert mrr(results, "not present anywhere") == 0.0


def test_recall_at_token_budget_stops_after_exceeding():
    evidence = "the exact evidence text"
    results = [FakeResult("aaa"), FakeResult("prefix " + evidence + " suffix"), FakeResult("bbb")]
    token_counts = [100, 100, 100]
    assert recall_at_token_budget(results, evidence, 150, token_counts) is True
    assert recall_at_token_budget(results, evidence, 50, token_counts) is False


def test_split_by_type_and_table():
    class Case(NamedTuple):
        type: str
        table_id: str | None

    cases = [Case("fact_lookup", None), Case("fact_lookup", "t1"), Case("number_from_table", "t2")]
    values = [1.0, 0.0, 1.0]
    by_type = split_by_type(cases, values)
    assert by_type["fact_lookup"] == [1.0, 0.0]
    assert by_type["number_from_table"] == [1.0]
    by_table = split_by_table(cases, values)
    assert by_table["prose"] == [1.0]
    assert by_table["table"] == [0.0, 1.0]


# --------------------------------------------------------------------------
# bootstrap CIs
# --------------------------------------------------------------------------


def test_bootstrap_ci_is_deterministic_for_a_fixed_seed():
    values = [0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    ci_a = bootstrap_ci(values, n=500, seed=42)
    ci_b = bootstrap_ci(values, n=500, seed=42)
    assert ci_a == ci_b
    lo, hi = ci_a
    assert 0.0 <= lo <= hi <= 1.0


def test_bootstrap_ci_single_value():
    assert bootstrap_ci([0.7]) == (0.7, 0.7)


def test_bootstrap_diff_ci_requires_paired_lengths():
    with pytest.raises(ValueError):
        bootstrap_diff_ci([1.0, 0.0], [1.0])


def test_bootstrap_diff_ci_zero_when_identical():
    values = [1.0, 0.0, 1.0, 1.0, 0.0]
    lo, hi = bootstrap_diff_ci(values, values, seed=1)
    assert lo == pytest.approx(0.0)
    assert hi == pytest.approx(0.0)


# --------------------------------------------------------------------------
# golden set validation
# --------------------------------------------------------------------------


def test_golden_fixture_validates_clean():
    problems = validate(FIXTURE_GOLDEN, FIXTURE_PARSED_DIR)
    assert problems == []


def test_load_golden_returns_five_fixture_cases():
    cases = load_golden(FIXTURE_GOLDEN)
    assert len(cases) == 5
    assert {c.type for c in cases} == {
        "fact_lookup",
        "number_from_table",
        "exact_term",
        "unanswerable",
        "multi_part",
    }


def test_validate_catches_bad_evidence(tmp_path):
    bad_case = {
        "id": "bad0001",
        "question": "What does the filing say?",
        "type": "fact_lookup",
        "companies": ["0001111111"],
        "accession_no": "0001111111-25-000001",
        "item": "1A",
        "section_id": "0001111111-25-000001:1A:001",
        "table_id": None,
        "evidence": "This text does not appear anywhere in the fixture filing.",
        "page": 2,
        "answer": "Some answer.",
        "answer_kind": "text",
        "answer_value": None,
        "notes": "",
    }
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(bad_case) + "\n", encoding="utf-8")
    problems = validate(path, FIXTURE_PARSED_DIR)
    assert len(problems) == 1
    assert "not an exact substring" in problems[0].reason


def test_validate_catches_wrong_page(tmp_path):
    case = json.loads(next(iter(FIXTURE_GOLDEN.read_text().splitlines())))
    case["page"] = 3  # section 1A:001 is on page 2
    path = tmp_path / "bad_page.jsonl"
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")
    problems = validate(path, FIXTURE_PARSED_DIR)
    assert any("page" in p.reason for p in problems)


def test_validate_catches_duplicate_ids(tmp_path):
    lines = FIXTURE_GOLDEN.read_text().splitlines()
    path = tmp_path / "dup.jsonl"
    path.write_text(lines[0] + "\n" + lines[0] + "\n", encoding="utf-8")
    problems = validate(path, FIXTURE_PARSED_DIR)
    assert any("duplicate id" in p.reason for p in problems)


def test_validate_catches_company_mismatch(tmp_path):
    case = json.loads(FIXTURE_GOLDEN.read_text().splitlines()[0])
    case["companies"] = ["0009999999"]
    path = tmp_path / "bad_company.jsonl"
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")
    problems = validate(path, FIXTURE_PARSED_DIR)
    assert any("does not match filing CIK" in p.reason for p in problems)


# --------------------------------------------------------------------------
# judge: JSON parsing with FakeJudge
# --------------------------------------------------------------------------


def test_correctness_with_fake_judge():
    fake = FakeJudge('{"reason": "matches the reference", "label": "correct"}')
    result = judge.correctness(fake, "Q?", "reference answer", "model answer")
    assert result.label == "correct"
    assert result.reason == "matches the reference"
    assert result.model == "fake"
    assert result.prompt_version == "v1"


def test_correctness_rejects_unknown_label():
    fake = FakeJudge('{"reason": "hmm", "label": "sort_of"}')
    with pytest.raises(JudgeParseError):
        judge.correctness(fake, "Q?", "ref", "ans")


def test_correctness_rejects_malformed_json():
    fake = FakeJudge("not json at all")
    with pytest.raises(JudgeParseError):
        judge.correctness(fake, "Q?", "ref", "ans")


def test_correctness_rejects_missing_reason_key():
    fake = FakeJudge('{"label": "correct"}')
    with pytest.raises(JudgeParseError):
        judge.correctness(fake, "Q?", "ref", "ans")


def test_faithfulness_full_pipeline_with_fake_judge():
    split_response = '{"reason": "two claims found", "claims": ["Revenue grew.", "Costs fell."]}'
    claim_responses = [
        '{"reason": "text confirms growth", "label": "supported"}',
        '{"reason": "no mention of costs", "label": "not_supported"}',
    ]
    fake = FakeJudge([split_response] + claim_responses)
    judgments, score = judge.faithfulness(fake, "Revenue grew. Costs fell.", "Revenue increased this year.")
    assert [j.label for j in judgments] == ["supported", "not_supported"]
    assert score == pytest.approx(0.5)


def test_citation_support_with_fake_judge():
    fake = FakeJudge('{"reason": "chunk states this directly", "label": "supports"}')
    result = judge.citation_support(fake, "Revenue grew.", "Revenue increased in fiscal 2025.")
    assert result.label == "supports"


def test_refusal_unanswerable_case():
    fake = FakeJudge('{"reason": "model says filing is silent", "label": "refused"}')
    result = judge.refusal(fake, "What is FY2026 guidance?", "The filing does not state this.", is_unanswerable=True)
    assert result.label == "refused"


def test_refusal_rejects_label_from_the_wrong_set():
    # "refused" is only valid for unanswerable cases; here is_unanswerable=False.
    fake = FakeJudge('{"reason": "model refused", "label": "refused"}')
    with pytest.raises(JudgeParseError):
        judge.refusal(fake, "What was revenue?", "I cannot say.", is_unanswerable=False)


# --------------------------------------------------------------------------
# router metrics
# --------------------------------------------------------------------------


def test_router_accuracy_exact_set_match():
    predicted = [["0001111111"], ["0002222222"], "general"]
    golden = [["0001111111"], ["0003333333"], "general"]
    result = router_metrics.router_accuracy(predicted, golden, seed=1)
    assert result["value"] == pytest.approx(2 / 3)
    lo, hi = result["ci95"]
    assert 0.0 <= lo <= hi <= 1.0


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------


def test_export_sheet_stratified_and_holdout(tmp_path):
    answers_path = tmp_path / "answers.jsonl"
    rows = []
    for i in range(20):
        rows.append(
            {
                "run_id": "run-a" if i % 2 == 0 else "run-b",
                "type": "fact_lookup" if i % 3 == 0 else "number_from_table",
                "id": f"q{i}",
                "question": f"Question {i}?",
                "reference": "ref",
                "answer": "ans",
                "retrieved_text_excerpt": "excerpt",
            }
        )
    answers_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    out_dir = tmp_path / "calibration"
    result = calibration.export_sheet(answers_path, n=10, seed=0, holdout=3, out_dir=out_dir)
    assert result["sheet"].exists()
    assert result["holdout"].exists()

    with result["sheet"].open() as f:
        sheet_rows = list(__import__("csv").DictReader(f))
    with result["holdout"].open() as f:
        holdout_rows = list(__import__("csv").DictReader(f))
    assert len(sheet_rows) == 7
    assert len(holdout_rows) == 3
    assert set(sheet_rows[0].keys()) == set(calibration.SHEET_COLUMNS)


def test_agreement_accuracy_and_kappa(tmp_path):
    sheet_path = tmp_path / "sheet.csv"
    with sheet_path.open("w", newline="") as f:
        writer = __import__("csv").DictWriter(f, fieldnames=calibration.SHEET_COLUMNS)
        writer.writeheader()
        writer.writerow({"id": "q1", "question": "", "reference": "", "answer": "", "retrieved_text_excerpt": "", "human_label": "correct"})
        writer.writerow({"id": "q2", "question": "", "reference": "", "answer": "", "retrieved_text_excerpt": "", "human_label": "incorrect"})
        writer.writerow({"id": "q3", "question": "", "reference": "", "answer": "", "retrieved_text_excerpt": "", "human_label": ""})
    judge_labels = {"q1": "correct", "q2": "correct", "q3": "correct"}
    result = calibration.agreement(sheet_path, judge_labels)
    assert result["accuracy"] == pytest.approx(0.5)
    assert "confusion" in result


def test_consistency_share_identical_labels():
    responses = [
        '{"reason": "a", "label": "correct"}',
        '{"reason": "a", "label": "correct"}',
        '{"reason": "a", "label": "correct"}',
        '{"reason": "b", "label": "correct"}',
        '{"reason": "b", "label": "partial"}',
        '{"reason": "b", "label": "correct"}',
    ]
    fake = FakeJudge(responses)
    share = calibration.consistency(fake, cases=["prompt one", "prompt two"], runs=3)
    assert share == pytest.approx(0.5)


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def test_run_eval_refuses_test_split_without_unseal():
    retriever = make_random_retriever(FIXTURE_PARSED_DIR, seed=0)
    with pytest.raises(PermissionError):
        run_eval({"k": 8}, "test", retriever, golden_path=FIXTURE_GOLDEN, parsed_dir=FIXTURE_PARSED_DIR)


def test_run_eval_rejects_unknown_split():
    retriever = make_random_retriever(FIXTURE_PARSED_DIR, seed=0)
    with pytest.raises(ValueError):
        run_eval({"k": 8}, "train", retriever, golden_path=FIXTURE_GOLDEN, parsed_dir=FIXTURE_PARSED_DIR)


def test_run_eval_end_to_end_with_random_retriever(tmp_path):
    retriever = make_random_retriever(FIXTURE_PARSED_DIR, seed=0)
    record = run_eval(
        {"index": "random", "search": "random", "reranker": "none", "k": 8, "top": 8, "router": False},
        "dev",
        retriever,
        golden_path=FIXTURE_GOLDEN,
        parsed_dir=FIXTURE_PARSED_DIR,
        results_dir=tmp_path,
    )
    assert record.split == "dev"
    assert record.n_questions == 5
    assert "recall@8" in record.metrics
    assert "mrr" in record.metrics
    assert 0.0 <= record.metrics["recall@8"]["value"] <= 1.0

    runs_file = tmp_path / "runs.jsonl"
    assert runs_file.exists()
    lines = runs_file.read_text().strip().splitlines()
    assert len(lines) == 1
    logged = json.loads(lines[0])
    assert logged["run_id"] == record.run_id
    assert logged["split"] == "dev"

    detail_file = tmp_path / f"{record.run_id}.jsonl"
    assert detail_file.exists()
    detail_rows = [json.loads(line) for line in detail_file.read_text().strip().splitlines()]
    assert len(detail_rows) == 5
    assert {"id", "hit_rank", "top_result_ids"} <= set(detail_rows[0].keys())


def test_report_compare_two_runs(tmp_path):
    retriever = make_random_retriever(FIXTURE_PARSED_DIR, seed=0)
    record_a = run_eval(
        {"index": "a", "search": "random", "reranker": "none", "k": 8, "top": 8, "router": False},
        "dev",
        retriever,
        golden_path=FIXTURE_GOLDEN,
        parsed_dir=FIXTURE_PARSED_DIR,
        results_dir=tmp_path,
    )
    record_b = run_eval(
        {"index": "b", "search": "random", "reranker": "none", "k": 8, "top": 8, "router": False},
        "dev",
        retriever,
        golden_path=FIXTURE_GOLDEN,
        parsed_dir=FIXTURE_PARSED_DIR,
        results_dir=tmp_path,
    )
    table = report.compare([record_a.run_id, record_b.run_id], results_dir=tmp_path)
    assert record_a.run_id in table
    assert record_b.run_id in table
    assert "recall@8" in table

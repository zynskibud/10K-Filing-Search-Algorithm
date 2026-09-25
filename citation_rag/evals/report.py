"""Compare eval runs: a markdown table of metrics plus paired-difference CIs.

CLI:
    uv run python -m citation_rag.evals.report <run_id> [<run_id> ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from citation_rag.evals.retrieval_metrics import bootstrap_diff_ci

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"


def _load_runs(run_ids: list[str], results_dir: Path) -> list[dict[str, Any]]:
    path = results_dir / "runs.jsonl"
    found: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["run_id"] in run_ids:
                found[rec["run_id"]] = rec
    missing = [rid for rid in run_ids if rid not in found]
    if missing:
        raise ValueError(f"run_id(s) not found in {path}: {missing}")
    return [found[rid] for rid in run_ids]


def _load_detail(run_id: str, results_dir: Path) -> dict[str, dict[str, Any]]:
    path = results_dir / f"{run_id}.jsonl"
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


def _per_question_recall(detail: dict[str, dict[str, Any]], k: int) -> dict[str, float]:
    return {
        qid: (1.0 if row["hit_rank"] is not None and row["hit_rank"] <= k else 0.0)
        for qid, row in detail.items()
    }


def _per_question_mrr(detail: dict[str, dict[str, Any]]) -> dict[str, float]:
    return {qid: (1.0 / row["hit_rank"] if row["hit_rank"] else 0.0) for qid, row in detail.items()}


def _fmt_metric(value: Any) -> str:
    if isinstance(value, dict) and "ci95" in value:
        return f"{value['value']:.3f} [{value['ci95'][0]:.3f}, {value['ci95'][1]:.3f}]"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def compare(run_ids: list[str], results_dir: Path | str = RESULTS_DIR) -> str:
    """Build a markdown comparison of the given runs (by run_id, in order given).

    First table: metric, value + CI per run. Second table: paired-difference CI
    between the first run_id and each other run_id, for recall@k and mrr,
    recomputed from the per-question detail files (matched by question id).
    """
    results_dir = Path(results_dir)
    runs = _load_runs(run_ids, results_dir)

    metric_keys: list[str] = []
    for run in runs:
        for key in run["metrics"]:
            if key not in metric_keys:
                metric_keys.append(key)

    lines: list[str] = []
    header = "| Metric | " + " | ".join(run_ids) + " |"
    sep = "|" + "---|" * (len(run_ids) + 1)
    lines.append(header)
    lines.append(sep)
    for key in metric_keys:
        row = [key]
        for run in runs:
            row.append(_fmt_metric(run["metrics"].get(key, "n/a")))
        lines.append("| " + " | ".join(row) + " |")

    base = runs[0]
    base_k = base["config"].get("k", 8)
    base_detail = _load_detail(base["run_id"], results_dir)
    base_recall = _per_question_recall(base_detail, base_k)
    base_mrr = _per_question_mrr(base_detail)

    diff_lines: list[str] = []
    if len(runs) > 1 and base_detail:
        diff_lines.append("")
        diff_lines.append(f"### Paired difference vs {base['run_id']}")
        diff_lines.append("| Run | recall@k diff (95% CI) | mrr diff (95% CI) |")
        diff_lines.append("|---|---|---|")
        for run in runs[1:]:
            other_k = run["config"].get("k", base_k)
            other_detail = _load_detail(run["run_id"], results_dir)
            if not other_detail:
                diff_lines.append(f"| {run['run_id']} | n/a (no detail file) | n/a |")
                continue
            other_recall = _per_question_recall(other_detail, other_k)
            other_mrr = _per_question_mrr(other_detail)
            common = sorted(set(base_recall) & set(other_recall))
            if not common:
                diff_lines.append(f"| {run['run_id']} | n/a (no shared questions) | n/a |")
                continue
            a_recall = [base_recall[q] for q in common]
            b_recall = [other_recall[q] for q in common]
            a_mrr = [base_mrr[q] for q in common]
            b_mrr = [other_mrr[q] for q in common]
            lo_r, hi_r = bootstrap_diff_ci(a_recall, b_recall)
            lo_m, hi_m = bootstrap_diff_ci(a_mrr, b_mrr)
            diff_lines.append(
                f"| {run['run_id']} | [{lo_r:.3f}, {hi_r:.3f}] | [{lo_m:.3f}, {hi_m:.3f}] |"
            )

    return "\n".join(lines + diff_lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.evals.report")
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args(argv)
    print(compare(args.run_ids, results_dir=args.results_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())

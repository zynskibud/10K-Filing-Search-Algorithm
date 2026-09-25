"""Calibration tooling for the judge: export a labeling sheet, compute human/judge
agreement, and check judge label consistency across repeated runs.

There is no formal schema for "model answers" yet (wave 7 produces them). Until
then, `answers_path` is a JSONL file where each line has: run_id, id, type,
question, reference, answer, retrieved_text_excerpt. See "judgment calls" in
reports/wave-3b.md.

CLI:
    uv run python -m citation_rag.evals.calibration export answers.jsonl --n 40 --holdout 15
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from citation_rag.evals.judge import JudgeClient, _parse_json_object

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_DIR = PROJECT_ROOT / "evals" / "calibration"

SHEET_COLUMNS = ["id", "question", "reference", "answer", "retrieved_text_excerpt", "human_label"]


def _load_answers(answers_path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(answers_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_sheet_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row.get("id", ""),
                    "question": row.get("question", ""),
                    "reference": row.get("reference", ""),
                    "answer": row.get("answer", ""),
                    "retrieved_text_excerpt": row.get("retrieved_text_excerpt", ""),
                    "human_label": "",
                }
            )


def export_sheet(
    answers_path: str | Path,
    n: int = 40,
    seed: int = 0,
    holdout: int = 0,
    out_dir: str | Path | None = None,
) -> dict[str, Path]:
    """Sample n rows, stratified across (run_id, type), and write evals/calibration/sheet.csv.

    If holdout > 0, that many of the n sampled rows are written to a separate
    evals/calibration/holdout.csv instead, so they never enter the tuning set.
    Returns {"sheet": path} or {"sheet": path, "holdout": path}.
    """
    rows = _load_answers(answers_path)
    groups: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row.get("run_id"), row.get("type"))].append(row)

    rng = random.Random(seed)
    for group_rows in groups.values():
        rng.shuffle(group_rows)

    group_keys = sorted(groups.keys(), key=lambda k: (str(k[0]), str(k[1])))
    rng.shuffle(group_keys)

    selected: list[dict[str, Any]] = []
    while len(selected) < n and any(groups[k] for k in group_keys):
        for key in group_keys:
            if len(selected) >= n:
                break
            if groups[key]:
                selected.append(groups[key].pop())

    out_dir = Path(out_dir) if out_dir is not None else CALIBRATION_DIR
    holdout_rows: list[dict[str, Any]] = []
    tuning_rows = selected
    if holdout > 0:
        holdout_rows = selected[:holdout]
        tuning_rows = selected[holdout:]

    sheet_path = out_dir / "sheet.csv"
    _write_sheet_csv(sheet_path, tuning_rows)
    result = {"sheet": sheet_path}
    if holdout > 0:
        holdout_path = out_dir / "holdout.csv"
        _write_sheet_csv(holdout_path, holdout_rows)
        result["holdout"] = holdout_path
    return result


def agreement(sheet_with_labels: str | Path, judge_labels: dict[str, str]) -> dict[str, Any]:
    """Compare a human-labeled sheet (CSV with a filled human_label column) to judge labels.

    judge_labels maps question id -> judge label. Rows with an empty human_label,
    or with no matching judge label, are skipped. Returns accuracy, Cohen's kappa,
    and a confusion matrix (human label -> judge label -> count).
    """
    pairs: list[tuple[str, str]] = []
    with Path(sheet_with_labels).open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            human = (row.get("human_label") or "").strip()
            if not human:
                continue
            judge = judge_labels.get(row["id"])
            if judge is None:
                continue
            pairs.append((human, judge))

    if not pairs:
        return {"accuracy": 0.0, "cohen_kappa": 0.0, "confusion": {}}

    total = len(pairs)
    correct = sum(1 for h, j in pairs if h == j)
    accuracy = correct / total

    labels = sorted({h for h, _ in pairs} | {j for _, j in pairs})
    confusion: dict[str, dict[str, int]] = {h: {j: 0 for j in labels} for h in labels}
    for h, j in pairs:
        confusion[h][j] += 1

    human_counts = {l: sum(1 for h, _ in pairs if h == l) for l in labels}
    judge_counts = {l: sum(1 for _, j in pairs if j == l) for l in labels}
    pe = sum((human_counts[l] / total) * (judge_counts[l] / total) for l in labels)
    kappa = (accuracy - pe) / (1 - pe) if pe < 1 else 1.0

    return {"accuracy": accuracy, "cohen_kappa": kappa, "confusion": confusion}


def consistency(judge: JudgeClient, cases: list[str], runs: int = 3) -> float:
    """Share of cases with an identical label across `runs` repeated judge calls.

    Each entry in `cases` is a fully-rendered judge prompt (as produced by one
    of the judge.py prompt templates). This is deliberately generic across the
    four judgment types, since consistency-checking does not depend on which
    one is being calibrated.
    """
    if not cases:
        return 0.0
    identical = 0
    for prompt in cases:
        labels = []
        for _ in range(runs):
            raw = judge.complete(prompt)
            data = _parse_json_object(raw)
            labels.append(data.get("label"))
        if len(set(labels)) == 1:
            identical += 1
    return identical / len(cases)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citation_rag.evals.calibration")
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="export a stratified labeling sheet")
    p_export.add_argument("answers_path")
    p_export.add_argument("--n", type=int, default=40)
    p_export.add_argument("--seed", type=int, default=0)
    p_export.add_argument("--holdout", type=int, default=0)

    args = parser.parse_args(argv)
    if args.command == "export":
        result = export_sheet(args.answers_path, n=args.n, seed=args.seed, holdout=args.holdout)
        for key, path in result.items():
            print(f"{key}: {path}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

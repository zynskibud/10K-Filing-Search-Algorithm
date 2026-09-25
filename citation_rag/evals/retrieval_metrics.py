"""Retrieval metrics: hit detection, recall@k, MRR, recall at a token budget,
splits by question type and prose vs table, and bootstrap confidence intervals.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from difflib import SequenceMatcher
from statistics import mean
from typing import Any, Iterable, Sequence

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse whitespace runs to a single space and strip. Case is kept."""
    return _WS_RE.sub(" ", text).strip()


def is_hit(chunk_text: str, evidence: str) -> bool:
    """True if the chunk covers at least 80% of the evidence's characters.

    A full normalized-substring match is always a hit. Otherwise the longest
    common substring between the normalized evidence and the normalized chunk
    must cover at least 80% of the (normalized) evidence length.
    """
    if not evidence:
        return False
    ev = _normalize(evidence)
    ch = _normalize(chunk_text)
    if not ev:
        return False
    if ev in ch:
        return True
    matcher = SequenceMatcher(None, ev, ch, autojunk=False)
    match = matcher.find_longest_match(0, len(ev), 0, len(ch))
    return (match.size / len(ev)) >= 0.8


def recall_at_k(results: Sequence[Any], evidence: str, k: int) -> bool:
    """True if any of the first k results is a hit for the evidence."""
    for r in results[:k]:
        if is_hit(r.text, evidence):
            return True
    return False


def mrr(results: Sequence[Any], evidence: str) -> float:
    """Reciprocal rank (1-indexed) of the first hit, or 0.0 if there is none."""
    for i, r in enumerate(results, start=1):
        if is_hit(r.text, evidence):
            return 1.0 / i
    return 0.0


def hit_rank(results: Sequence[Any], evidence: str) -> int | None:
    """1-indexed rank of the first hit, or None if there is none."""
    for i, r in enumerate(results, start=1):
        if is_hit(r.text, evidence):
            return i
    return None


def recall_at_token_budget(
    results: Sequence[Any],
    evidence: str,
    budget_tokens: int,
    token_counts: Sequence[int],
) -> bool:
    """Walk results in order, stop once the running token sum exceeds the budget.

    Returns True if any result included in that walk is a hit.
    """
    included = []
    total = 0
    for r, tc in zip(results, token_counts):
        included.append(r)
        total += tc
        if total > budget_tokens:
            break
    return any(is_hit(r.text, evidence) for r in included)


def split_by_type(cases: Sequence[Any], values: Sequence[Any]) -> dict[str, list[Any]]:
    """Group values by case.type."""
    groups: dict[str, list[Any]] = defaultdict(list)
    for case, value in zip(cases, values):
        groups[case.type].append(value)
    return dict(groups)


def split_by_table(cases: Sequence[Any], values: Sequence[Any]) -> dict[str, list[Any]]:
    """Group values into 'prose' and 'table' by whether case.table_id is set."""
    groups: dict[str, list[Any]] = {"prose": [], "table": []}
    for case, value in zip(cases, values):
        key = "table" if getattr(case, "table_id", None) else "prose"
        groups[key].append(value)
    return groups


def bootstrap_ci(values: Sequence[float], n: int = 1000, seed: int = 0) -> tuple[float, float]:
    """95% percentile bootstrap CI for the mean of values. Deterministic for a fixed seed."""
    values = list(values)
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (float(values[0]), float(values[0]))
    rng = random.Random(seed)
    n_vals = len(values)
    means = []
    for _ in range(n):
        sample = [values[rng.randrange(n_vals)] for _ in range(n_vals)]
        means.append(mean(sample))
    means.sort()
    lo = means[int(0.025 * n)]
    hi = means[min(int(0.975 * n), n - 1)]
    return (lo, hi)


def bootstrap_diff_ci(
    values_a: Sequence[float], values_b: Sequence[float], n: int = 1000, seed: int = 0
) -> tuple[float, float]:
    """95% percentile bootstrap CI for the paired mean difference (a - b), same questions."""
    if len(values_a) != len(values_b):
        raise ValueError("values_a and values_b must be paired (same length, same question order)")
    diffs = [a - b for a, b in zip(values_a, values_b)]
    return bootstrap_ci(diffs, n=n, seed=seed)

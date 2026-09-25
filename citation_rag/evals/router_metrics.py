"""Router accuracy: exact-set match between the predicted and golden company sets."""

from __future__ import annotations

from typing import Any, Sequence

from citation_rag.evals.retrieval_metrics import bootstrap_ci


def router_accuracy(
    predicted_companies: Sequence[Sequence[str] | str],
    golden_companies: Sequence[Sequence[str] | str],
    n: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    """Exact-set match per question, averaged, with a bootstrap 95% CI.

    Each element of predicted_companies / golden_companies is either the
    string "general" or a list of CIK strings; a question is a hit only if
    the two sets are exactly equal.
    """
    if len(predicted_companies) != len(golden_companies):
        raise ValueError("predicted_companies and golden_companies must be the same length")

    def _as_set(companies: Sequence[str] | str) -> frozenset[str] | str:
        if companies == "general":
            return "general"
        return frozenset(companies)

    hits = [
        1.0 if _as_set(pred) == _as_set(gold) else 0.0
        for pred, gold in zip(predicted_companies, golden_companies)
    ]
    if not hits:
        return {"value": 0.0, "ci95": (0.0, 0.0)}
    value = sum(hits) / len(hits)
    ci = bootstrap_ci(hits, n=n, seed=seed)
    return {"value": value, "ci95": ci}

"""Evaluation metrics.

Every function here is small enough to verify by hand, and each has a unit test with
a hand-computed expected value. That matters more than it looks: an evaluation whose
arithmetic is wrong is worse than no evaluation, because it produces confident numbers
that mean nothing.

Convention throughout: relevance is a *set* of acceptable ids, so a query with several
correct answers is not penalised for ranking one of them first.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence


def _relevant_set(relevant: Iterable) -> set:
    out = set()
    for item in relevant:
        if isinstance(item, (list, tuple, set)):
            out.update(item)
        else:
            out.add(item)
    return out


def recall_at_k(ranked: Sequence, relevant: Iterable, k: int) -> float:
    """Fraction of the relevant items that appear in the top `k`."""
    gold = _relevant_set(relevant)
    if not gold:
        return 0.0
    found = set(ranked[:k]) & gold
    return len(found) / len(gold)


def precision_at_k(ranked: Sequence, relevant: Iterable, k: int) -> float:
    """Fraction of the top `k` that is relevant."""
    top = list(ranked[:k])
    if not top:
        return 0.0
    gold = _relevant_set(relevant)
    return len(set(top) & gold) / len(top)


def hit_at_k(ranked: Sequence, relevant: Iterable, k: int = 1) -> float:
    """1.0 when at least one relevant item is in the top `k`."""
    gold = _relevant_set(relevant)
    return 1.0 if set(ranked[:k]) & gold else 0.0


def mrr(ranked: Sequence, relevant: Iterable) -> float:
    """Reciprocal rank of the first relevant item; 0.0 when there is none."""
    gold = _relevant_set(relevant)
    for position, item in enumerate(ranked, start=1):
        if item in gold:
            return 1.0 / position
    return 0.0


def ndcg_at_k(ranked: Sequence, relevant: Iterable, k: int) -> float:
    """Binary-gain nDCG.

    Gain is 1 for a relevant item and 0 otherwise. `k` truncates both the ranking and
    the ideal ranking, so a system that returns nothing scores 0 rather than dividing
    by an empty ideal.
    """
    gold = _relevant_set(relevant)
    if not gold:
        return 0.0

    dcg = 0.0
    for position, item in enumerate(ranked[:k], start=1):
        if item in gold:
            dcg += 1.0 / math.log2(position + 1)

    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def precision_recall_f1(
    truth: Sequence, predicted: Sequence, *, true_positives: int | None = None
) -> dict[str, float]:
    """Per-class precision, recall and F1 for one label.

    `truth` and `predicted` are *independent* sets -- every actual of this label, and
    every prediction of it -- so they are not the same length and cannot be zipped.

    `true_positives` must be supplied to be exact. The `min(len, len)` fallback is
    only right when one set contains the other; `per_class_report` passes the count
    from the confusion matrix, which is always right.
    """
    truth = list(truth)
    predicted = list(predicted)
    tp = min(len(truth), len(predicted)) if true_positives is None else true_positives
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(truth) if truth else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "support": len(truth), "tp": tp}


def per_class_report(
    truth: Sequence[str], predicted: Sequence[str], labels: Sequence[str] | None = None
) -> dict[str, dict[str, float]]:
    """Precision/recall/F1 for every label, plus a macro average.

    Averaging over the classes *present* rather than over a fixed vocabulary: a class
    with no examples carries no information, and including it would drag every macro
    score toward zero as labels are added.
    """
    truth = list(truth)
    predicted = list(predicted)
    names = list(labels) if labels else sorted(set(truth) | set(predicted))
    report = {
        label: precision_recall_f1([t for t in truth if t == label], [p for p in predicted if p == label])
        for label in names
    }
    seen = [label for label in names if report[label]["support"]]
    report["macro"] = {
        "precision": sum(report[label]["precision"] for label in seen) / len(seen) if seen else 0.0,
        "recall": sum(report[label]["recall"] for label in seen) / len(seen) if seen else 0.0,
        "f1": sum(report[label]["f1"] for label in seen) / len(seen) if seen else 0.0,
        "support": sum(report[label]["support"] for label in seen),
    }
    return report


def macro_f1(truth: Sequence[str], predicted: Sequence[str]) -> float:
    return per_class_report(truth, predicted)["macro"]["f1"]


def confusion_matrix(
    truth: Sequence[str], predicted: Sequence[str], labels: Sequence[str] | None = None
) -> dict[str, dict[str, int]]:
    """`matrix[truth_label][predicted_label]` counts."""
    truth = list(truth)
    predicted = list(predicted)
    names = list(labels) if labels else sorted(set(truth) | set(predicted))
    matrix = {t: {p: 0 for p in names} for t in names}
    for actual, guess in zip(truth, predicted, strict=True):
        matrix.setdefault(actual, {p: 0 for p in names})
        matrix[actual][guess] = matrix[actual].get(guess, 0) + 1
    return matrix


def percentiles(values: Sequence[float], points: Sequence[float] = (50, 95)) -> dict[str, float]:
    """Nearest-rank percentiles.

    Nearest-rank rather than interpolating: with 20 latency samples, "p95 is the
    95th slowest observed value" is the thing an operator can act on, and it never
    invents a value that was not measured.
    """
    if not values:
        return {f"p{int(p)}": 0.0 for p in points}
    ordered = sorted(values)
    out: dict[str, float] = {}
    for point in points:
        if point <= 0:
            out[f"p{int(point)}"] = float(ordered[0])
            continue
        rank = math.ceil(point / 100 * len(ordered))
        index = min(max(rank - 1, 0), len(ordered) - 1)
        out[f"p{int(point)}"] = float(ordered[index])
    return out


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def average_metrics(rows: Iterable[Mapping[str, float]], key: str) -> float:
    """Mean of one metric across runs -- the number a report should headline."""
    values = [row[key] for row in rows if key in row]
    return mean(values)


__all__ = [
    "average_metrics",
    "confusion_matrix",
    "hit_at_k",
    "macro_f1",
    "mean",
    "mrr",
    "ndcg_at_k",
    "per_class_report",
    "percentiles",
    "precision_at_k",
    "precision_recall_f1",
    "recall_at_k",
]

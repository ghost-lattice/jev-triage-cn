from __future__ import annotations

from dataclasses import dataclass
import math


Z = 1.959963984540054


def wilson_lower(correct: int, total: int) -> float:
    if not total: return 0.0
    p = correct / total; z2 = Z * Z
    return ((p + z2/(2*total) - Z * math.sqrt(p*(1-p)/total + z2/(4*total*total))) / (1 + z2/total))


def best_case_minimum(target: float) -> int:
    return math.ceil(target * Z * Z / (1 - target))


@dataclass
class Threshold:
    metric: str
    threshold: float | None
    count: int
    correct: int
    lower: float
    feasible: bool
    required: int


def fit_threshold(records: list[dict], metric: str, target: float, minimum: int = 30) -> Threshold:
    required = max(minimum, best_case_minimum(target))
    values = sorted({float(record[metric]) for record in records})
    feasible = []
    for value in values:
        subset = [record for record in records if record[metric] >= value]
        correct = sum(record["correct"] for record in subset)
        lower = wilson_lower(correct, len(subset))
        if len(subset) >= required and lower >= target:
            feasible.append((value, subset, correct, lower))
    if not feasible:
        # For an infeasible metric, report the actual largest candidate set (all
        # records at the lowest observed value), rather than inventing zero errors.
        subset = [record for record in records if record[metric] >= values[0]] if values else []
        correct = sum(record["correct"] for record in subset)
        return Threshold(metric, None, len(subset), correct, wilson_lower(correct, len(subset)), False, required)
    value, subset, correct, lower = feasible[0]  # lowest qualifying threshold
    return Threshold(metric, value, len(subset), correct, lower, True, required)


def evaluate(records: list[dict], threshold: Threshold) -> dict:
    if threshold.threshold is None: return {"count": 0, "coverage": 0.0, "correct": 0, "accuracy": 0.0, "lower": 0.0}
    subset = [record for record in records if record[threshold.metric] >= threshold.threshold]
    correct = sum(record["correct"] for record in subset)
    return {"count": len(subset), "coverage": len(subset)/len(records) if records else 0.0, "correct": correct, "accuracy": correct/len(subset) if subset else 0.0, "lower": wilson_lower(correct, len(subset))}


def confidence_bins(records: list[dict]) -> list[tuple[str, int, float]]:
    result = []
    for label, low, high in (("0.0–0.3", 0.0, 0.3), ("0.3–0.5", 0.3, 0.5), ("0.5–0.6", 0.5, 0.6), ("0.6–0.7", 0.6, 0.7), ("0.7–0.8", 0.7, 0.8), ("0.8–0.9", 0.8, 0.9), ("0.9–1.0", 0.9, 1.0)):
        subset = [row for row in records if low <= row["confidence"] < high]
        result.append((label, len(subset), sum(row["correct"] for row in subset)/len(subset) if subset else 0.0))
    subset = [row for row in records if row["confidence"] >= 1.0]
    result.append(("1.0", len(subset), sum(row["correct"] for row in subset)/len(subset) if subset else 0.0))
    return result


def threshold_failure_message(thresholds: list[Threshold], target: float) -> str:
    """Explain whether failure is caused by candidate size or observed errors."""
    largest = max(thresholds, key=lambda item: item.count)
    if largest.count < largest.required:
        return f"样本量不足，还需要至少 {largest.required - largest.count} 条。"
    strongest = max(thresholds, key=lambda item: item.lower)
    return f"拟合集 {strongest.count} 条中 {strongest.correct} 条正确，Wilson 下界 {strongest.lower:.3f}，低于目标 {target:.2f}；错误数导致无法达标，与样本量无关。"


def fit_size_limitation(total_records: int, target: float) -> str:
    fit_size = total_records // 2
    required = max(30, best_case_minimum(target))
    return f"本报告将 {total_records} 条样本按 1:1 拆分，拟合集为 {fit_size} 条；目标 {target:.2f} 的最低拟合样本门槛为 {required} 条。"

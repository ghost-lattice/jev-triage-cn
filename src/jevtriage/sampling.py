from __future__ import annotations

import random


def stratified_sample(rows: list[dict[str, str]], size: int | None, seed: int) -> list[dict[str, str]]:
    """Equal-per-label sampling with at most one ambiguous example per label."""
    if size is None or size >= len(rows):
        return rows
    labels = sorted({row.get("label", "") for row in rows})
    if not labels or "" in labels or size < len(labels):
        raise ValueError("--sample requires label values and must be at least the label count")
    rng = random.Random(seed)
    quotient, remainder = divmod(size, len(labels))
    selected = []
    for index, label in enumerate(labels):
        count = quotient + (1 if index < remainder else 0)
        group = [row for row in rows if row["label"] == label]
        if count > len(group):
            raise ValueError(f"--sample asks for too many {label} samples")
        ambiguous = [row for row in group if row.get("ambiguous") == "true"]
        others = [row for row in group if row.get("ambiguous") != "true"]
        rng.shuffle(ambiguous); rng.shuffle(others)
        # One ambiguous maximum: remaining positions are normal rows, then a
        # fallback to additional ambiguous rows only when normal rows run out.
        picked = ambiguous[:1] if ambiguous and count else []
        picked.extend(others[:count-len(picked)])
        picked.extend(ambiguous[1:count-len(picked)])
        selected.extend(picked)
    rng.shuffle(selected)
    return selected

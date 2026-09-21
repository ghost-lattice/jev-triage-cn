"""Shared provenance policy for the phase-3 report command."""
from __future__ import annotations


def report_title(backends: set[str]) -> str:
    """Mock-derived reports are demonstrations and must never make accuracy promises."""
    return "MOCK 演示数据，无意义" if "mock" in backends else "Jev 分流校准报告"


def permits_accuracy_commitment(backends: set[str]) -> bool:
    return "mock" not in backends

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

from .backends import JevBackend, MockBackend
from .cache import ResultCache
from .config import load_config
from .sampling import stratified_sample
from .runlock import RunLock

# Calibrated on the 2026-09-21 10-row Jev validation: 6,688 actual / 3,418 raw.
# Revisit after a larger real sample; returned usage remains authoritative.
ESTIMATOR_CORRECTION = 1.956699824458748


def _rows(path: str, text_column: str, limit: int | None) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or text_column not in rows[0]:
        raise ValueError(f"input CSV needs a {text_column!r} column")
    return rows[:limit] if limit is not None else rows


def _estimate_tokens(rows: list[dict[str, str]], text_column: str, config) -> int:
    # Deliberately conservative character-based approximation; only API usage is authoritative.
    question_chars = sum(len(question.instructions) + sum(len(key) + len(value) for key, value in question.options.items()) for question in (config.classification, *config.additional_questions))
    raw = sum(max(1, len(row[text_column]) + question_chars) for row in rows)
    return round(raw * ESTIMATOR_CORRECTION)


def run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    rows = _rows(args.input, config.text_column, args.limit)
    if args.sample is not None:
        rows = stratified_sample(rows, args.sample, args.seed)
        print(json.dumps({"sampled": [{"id": row.get("id"), "ambiguous": row.get("ambiguous", "false")} for row in rows]}, ensure_ascii=False))
    cache = ResultCache()
    uncached = [row for row in rows if cache.get(row[config.text_column], config.digest, args.backend, config.model) is None]
    print(json.dumps({"rows": len(rows), "cached": len(rows) - len(uncached), "would_send": len(uncached), "estimated_input_tokens": _estimate_tokens(uncached, config.text_column, config), "estimate_note": "粗略估算，以返回的 usage 为准", "backend": args.backend, "model_requested": config.model}, ensure_ascii=False))
    if args.dry_run:
        return 0
    if args.backend == "jev" and uncached and not args.yes:
        answer = input(f"将向 Jev 发送 {len(uncached)} 条文本（每条一次请求）。继续？[y/N] ")
        if answer.lower() not in {"y", "yes"}:
            print("已取消。", file=sys.stderr)
            return 2
    backend = MockBackend() if args.backend == "mock" else JevBackend(lambda name, usage: cache.log_request(args.run_id, name, usage))
    for number, row in enumerate(rows, 1):
        text = row[config.text_column]
        result = cache.get(text, config.digest, args.backend, config.model)
        cache_hit = result is not None
        if result is None:
            result = backend.triage(text, config)
            cache.put(text, config.digest, config.model, result)
        if number % 10 == 0:
            args.run_lock.heartbeat()
            print(json.dumps({"progress": {"processed": number, "total": len(rows)}}, ensure_ascii=False))
        print(json.dumps({"text": text, "cache_hit": cache_hit, "backend": result.backend, "model": result.model, "classification": result.classification.__dict__, "additional": {key: value.__dict__ for key, value in result.additional.items()}, "usage": result.usage}, ensure_ascii=False))
    if args.backend == "jev":
        summary = cache.request_summary(args.run_id); usage={"input_tokens":summary["input_tokens"],"output_tokens":summary["output_tokens"]}; cost = usage["input_tokens"] * 0.042 / 1_000_000
        print(json.dumps({"backend":"jev","actual_requests_this_run":summary["requests"],"cumulative_usage_this_run":usage,"estimated_input_cost_usd":round(cost,10),"price_usd_per_million_input_tokens":0.042},ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jevtriage")
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("run")
    command.add_argument("--config", required=True)
    command.add_argument("--input", required=True)
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--limit", type=int)
    command.add_argument("--sample", type=int, help="按标签分层抽样；每类尽量先抽取 ambiguous 样本")
    command.add_argument("--seed", type=int, default=42)
    command.add_argument("--backend", choices=("mock", "jev"), default="mock")
    command.add_argument("--yes", action="store_true")
    command.set_defaults(func=run)
    report_command = subparsers.add_parser("report")
    report_command.add_argument("--config", required=True)
    report_command.add_argument("--labeled", required=True)
    report_command.add_argument("--target", type=float, default=0.90)
    report_command.add_argument("--sample", type=int)
    report_command.add_argument("--seed", type=int, default=42)
    report_command.add_argument("--backend", choices=("mock", "jev"), default="mock")
    report_command.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    with RunLock() as run_lock:
        args.run_id = run_lock.run_id; args.run_lock = run_lock
        if args.command == "report":
            from .report_command import report
            return report(args, load_config(args.config))
        return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

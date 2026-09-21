from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .backends import ChoiceResult, TriageResult


class ResultCache:
    def __init__(self, path: str | Path = ".cache/jevtriage.sqlite3") -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS request_log (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, created_at REAL NOT NULL, backend TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL)")

    @staticmethod
    def key(text: str, config_digest: str, backend: str, requested_model: str) -> str:
        """Keep mock and Jev (and requested model versions) in separate namespaces."""
        return hashlib.sha256((text + "\0" + config_digest + "\0" + backend + "\0" + requested_model).encode()).hexdigest()

    def get(self, text: str, config_digest: str, backend: str, requested_model: str) -> TriageResult | None:
        row = self.db.execute("SELECT value FROM results WHERE key = ?", (self.key(text, config_digest, backend, requested_model),)).fetchone()
        if not row:
            return None
        raw = json.loads(row[0])
        classification = ChoiceResult(**raw["classification"])
        additional = {key: ChoiceResult(**value) for key, value in raw["additional"].items()}
        return TriageResult(raw["backend"], raw["model"], classification, additional, raw["usage"])

    def put(self, text: str, config_digest: str, requested_model: str, result: TriageResult) -> None:
        self.db.execute("INSERT OR REPLACE INTO results VALUES (?, ?)", (self.key(text, config_digest, result.backend, requested_model), json.dumps(result.as_dict(), ensure_ascii=False)))
        self.db.commit()

    def log_request(self, run_id: str, backend: str, usage: dict[str, int]) -> None:
        """Audit successful real requests only: never store text, headers, or secrets."""
        self.db.execute("INSERT INTO request_log (run_id, created_at, backend, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?)", (run_id, time.time(), backend, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))))
        self.db.commit()

    def request_summary(self, run_id: str) -> dict[str, int]:
        row = self.db.execute("SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM request_log WHERE run_id = ?", (run_id,)).fetchone()
        return {"requests": int(row[0]), "input_tokens": int(row[1]), "output_tokens": int(row[2])}

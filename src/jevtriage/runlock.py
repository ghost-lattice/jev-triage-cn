"""Single-process guard for cache-mutating CLI commands."""
from __future__ import annotations

import json, os, time, uuid
from pathlib import Path


class RunLock:
    timeout_seconds = 30 * 60

    def __init__(self, path: str | Path = ".cache/run.lock") -> None:
        self.path = Path(path); self.run_id = uuid.uuid4().hex; self.acquired = False

    @staticmethod
    def _alive(pid: int) -> bool:
        try: os.kill(pid, 0)
        except ProcessLookupError: return False
        except PermissionError: return True
        return True

    def _payload(self) -> str:
        return json.dumps({"pid": os.getpid(), "started_at": time.time(), "updated_at": time.time(), "run_id": self.run_id})

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(self._payload())
                self.acquired = True; return
            except FileExistsError:
                try: data = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError): data = {}
                updated = float(data.get("updated_at", self.path.stat().st_mtime if self.path.exists() else 0))
                stale = time.time() - updated > self.timeout_seconds
                if stale or not self._alive(int(data.get("pid", -1))):
                    self.path.unlink(missing_ok=True); continue
                raise RuntimeError(f"已有运行在执行（pid={data.get('pid')}）；锁文件：{self.path}")
        raise RuntimeError(f"无法接管锁文件：{self.path}")

    def heartbeat(self) -> None:
        if self.acquired:
            self.path.write_text(self._payload(), encoding="utf-8")

    def release(self) -> None:
        if self.acquired:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("run_id") == self.run_id: self.path.unlink(missing_ok=True)
            finally: self.acquired = False

    def __enter__(self): self.acquire(); return self
    def __exit__(self, *_): self.release()

"""Scan prospective Git content for likely secrets and local-identifying data.

Only file names and line numbers are emitted; matched text is never printed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


SECRET_PATTERNS = [
    re.compile(r"TYPESAFE_API_KEY\s*=\s*\S+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    re.compile(r"(?:sk-|ghp_|AKIA)[A-Za-z0-9_-]{16,}"),
    # A long identifier alone is common in source code; require a mixed token
    # shape before treating it as a likely credential.
    re.compile(r"(?<![A-Za-z0-9])(?=[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9]))(?=[A-Za-z0-9_+/=-]*[a-z])(?=[A-Za-z0-9_+/=-]*[A-Z])(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])"),
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)1\d{10}(?!\d)")
ABSOLUTE_PATH = re.compile(r"/(?:Users|home)/")
FORBIDDEN_NAMES = {".env", "run.lock"}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def candidate_files(root: Path) -> set[Path]:
    tracked = {Path(item) for item in _git(root, "ls-files", "-z").split("\0") if item}
    preview = _git(root, "add", "-n", "--", ".")
    added = {Path(match.group(1)) for match in re.finditer(r"add '([^']+)'", preview)}
    return tracked | added


def _dynamic_identifiers() -> set[str]:
    values = {socket.gethostname()}
    try: values.add(os.getlogin())
    except OSError: pass
    return {value for value in values if value}


def scan_text(text: str, identifiers: set[str], extra_terms: tuple[str, ...] = ()) -> list[int]:
    bad = []
    for number, line in enumerate(text.splitlines(), 1):
        matched = any(pattern.search(line) for pattern in SECRET_PATTERNS)
        matched = matched or ABSOLUTE_PATH.search(line) is not None or EMAIL.search(line) is not None
        matched = matched or PHONE.search(line) is not None
        matched = matched or any(identifier in line for identifier in identifiers)
        matched = matched or any(term in line for term in extra_terms)
        if matched: bad.append(number)
    return bad


def scan_files(root: Path, files: set[Path], extra_terms: tuple[str, ...] = ()) -> list[tuple[str, int]]:
    findings = []
    identifiers = _dynamic_identifiers()
    for relative in sorted(files):
        if relative.name in FORBIDDEN_NAMES or relative.suffix == ".key" or str(relative).startswith((".cache/", "reports/")):
            findings.append((str(relative), 0)); continue
        path = root / relative
        if not path.is_file(): continue
        try: text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError: findings.append((str(relative), 0)); continue
        findings.extend((str(relative), line) for line in scan_text(text, identifiers, extra_terms))
    return findings


def release_findings(root: Path) -> list[tuple[str, int]]:
    findings = []
    for name in ("README.md", "LICENSE", "pyproject.toml"):
        path = root / name
        if not path.exists(): continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(marker in line for marker in ("<COPYRIGHT HOLDER>", "PLACEHOLDER", "example.invalid")):
                findings.append((name, number))
    return findings


def history_identity_findings(history: str, allowed_email: str | None) -> list[int]:
    findings = []
    for number, line in enumerate(history.splitlines(), 1):
        if line.startswith(("Author:", "Commit:", "Committer:")):
            match = re.search(r"<([^>]+)>", line)
            if not match or match.group(1) != allowed_email: findings.append(number)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--history", action="store_true"); parser.add_argument("--release", action="store_true"); args = parser.parse_args(argv)
    root = Path.cwd()
    extra_terms = tuple(term.strip() for term in os.environ.get("PREFLIGHT_EXTRA", "").split(",") if term.strip())
    findings = scan_files(root, candidate_files(root), extra_terms)
    if args.release: findings.extend(release_findings(root))
    if args.history:
        history = _git(root, "log", "--format=fuller", "-p", "--no-ext-diff")
        # Identity lines have their own allow-list rule below; do not also
        # classify their permitted email addresses as generic email findings.
        history_body = "\n".join(line for line in history.splitlines() if not line.startswith(("Author:", "Commit:", "Committer:")))
        findings.extend(("git-history", line) for line in scan_text(history_body, _dynamic_identifiers(), extra_terms))
        findings.extend(("git-history", line) for line in history_identity_findings(history, os.environ.get("PREFLIGHT_ALLOWED_EMAIL")))
    for name, line in findings: print(f"{name}:{line}")
    return 1 if findings else 0


if __name__ == "__main__": raise SystemExit(main())

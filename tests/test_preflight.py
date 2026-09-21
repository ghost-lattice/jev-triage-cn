from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(preflight)


class PreflightTests(unittest.TestCase):
    def test_secret_finding_returns_line_number_without_value(self) -> None:
        text = "safe\n" + "TYPESAFE_API_KEY" + "=not-for-output\n"
        self.assertEqual(preflight.scan_text(text, set()), [2])

    def test_long_mixed_token_is_detected(self) -> None:
        token = "Ab1" + "x" * 29
        self.assertEqual(preflight.scan_text(token, set()), [1])

    def test_phone_is_detected_even_when_line_mentions_readme(self) -> None:
        self.assertEqual(preflight.scan_text("README " + "138" + "00138000", set()), [1])

    def test_extra_sensitive_term_is_detected_from_environment(self) -> None:
        previous = os.environ.get("PREFLIGHT_EXTRA")
        os.environ["PREFLIGHT_EXTRA"] = "internal-marker,another"
        try: self.assertEqual(preflight.scan_text("contains internal-marker", set(), tuple(os.environ["PREFLIGHT_EXTRA"].split(","))), [1])
        finally:
            if previous is None: os.environ.pop("PREFLIGHT_EXTRA", None)
            else: os.environ["PREFLIGHT_EXTRA"] = previous

    def test_release_placeholders_and_history_identity_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text("<COPYRIGHT HOLDER>", encoding="utf-8")
            self.assertEqual(preflight.release_findings(root), [("LICENSE", 1)])
        email = "person" + "@" + "example" + ".invalid"
        history = "Author: Name <" + email + ">\nCommitter: Name <" + "other" + "@" + "invalid.test>"
        self.assertEqual(preflight.history_identity_findings(history, email), [2])

    def test_path_email_and_absolute_path_are_detected(self) -> None:
        text = "a" + "@" + "example" + ".invalid\n" + "/" + "Users/example/file\n"
        self.assertEqual(preflight.scan_text(text, set()), [1, 2])

    def test_ignored_or_forbidden_file_is_reported_by_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("anything", encoding="utf-8")
            self.assertEqual(preflight.scan_files(root, {Path(".env")}), [(".env", 0)])


if __name__ == "__main__":
    unittest.main()

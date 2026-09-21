from __future__ import annotations

import csv
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jevtriage.backends import ChoiceResult, JevBackend, MockBackend, TriageResult
from jevtriage.cache import ResultCache
from jevtriage.cli import main
from jevtriage.config import load_config
from jevtriage.provenance import permits_accuracy_commitment, report_title
from jevtriage.reporting import best_case_minimum, confidence_bins, fit_size_limitation, fit_threshold, threshold_failure_message
from jevtriage.sampling import stratified_sample
from jevtriage.runlock import RunLock


ROOT = Path(__file__).parents[1]


class TriageTests(unittest.TestCase):
    def test_config_uses_fixed_model_and_choice_additional_question(self) -> None:
        config = load_config(ROOT / "configs/ecommerce.yaml")
        self.assertEqual(config.model, "jev-1.13.0")
        self.assertEqual(config.review_band, 0.15)
        self.assertEqual(config.additional_questions[0].id, "needs_human_24h")
        self.assertEqual(set(config.additional_questions[0].options), {"yes", "no"})

    def test_mock_is_repeatable_and_has_probability_fields(self) -> None:
        config = load_config(ROOT / "configs/ecommerce.yaml")
        one = MockBackend(seed=7).triage("这是合成评论", config)
        two = MockBackend(seed=7).triage("这是合成评论", config)
        self.assertEqual(one, two)
        self.assertAlmostEqual(sum(one.classification.probabilities.values()), 1.0)
        self.assertEqual(one.classification.top_probability, max(one.classification.probabilities.values()))
        self.assertGreaterEqual(one.classification.confidence, 0.0)

    def test_cache_roundtrip_keeps_full_results_without_credentials(self) -> None:
        config = load_config(ROOT / "configs/ecommerce.yaml")
        result = MockBackend().triage("仅用于缓存的合成文本", config)
        with tempfile.TemporaryDirectory() as directory:
            cache = ResultCache(Path(directory) / "cache.sqlite3")
            cache.put("仅用于缓存的合成文本", config.digest, config.model, result)
            self.assertEqual(cache.get("仅用于缓存的合成文本", config.digest, "mock", config.model), result)

    def test_request_log_sums_metadata_without_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = ResultCache(Path(directory) / "cache.sqlite3")
            cache.log_request("run-a", "jev", {"input_tokens": 12, "output_tokens": 3})
            cache.log_request("run-a", "jev", {"input_tokens": 8, "output_tokens": 4})
            self.assertEqual(cache.request_summary("run-a"), {"requests": 2, "input_tokens": 20, "output_tokens": 7})

    def test_run_lock_rejects_live_owner_and_reclaims_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.lock"
            path.write_text(json.dumps({"pid": os.getpid(), "updated_at": __import__("time").time()}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "已有运行"):
                RunLock(path).acquire()
            path.write_text(json.dumps({"pid": os.getpid(), "updated_at": 0}), encoding="utf-8")
            with RunLock(path) as lock:
                self.assertTrue(path.exists())
                lock.heartbeat()
            self.assertFalse(path.exists())

    def test_jev_does_not_read_mock_cache_and_sends_fake_http_request(self) -> None:
        config = load_config(ROOT / "configs/ecommerce.yaml")
        text = "同一条合成文本"
        with tempfile.TemporaryDirectory() as directory:
            cache = ResultCache(Path(directory) / "cache.sqlite3")
            cache.put(text, config.digest, config.model, MockBackend().triage(text, config))
            self.assertIsNone(cache.get(text, config.digest, "jev", config.model))

            class FakeResponse:
                def read(self):
                    return b'{"model":"jev-1.13.0","answers":{"category":{"type":"choice","choice":"positive","confidence":1.0,"probabilities":{"positive":1.0,"negative":0.0,"inquiry":0.0,"complaint":0.0,"other":0.0}},"needs_human_24h":{"type":"choice","choice":"no","confidence":1.0,"probabilities":{"yes":0.0,"no":1.0}}},"usage":{"input_tokens":12,"output_tokens":3}}'
                def __enter__(self): return self
                def __exit__(self, *args): return False

            with patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-only-not-a-real-key"}), patch("jevtriage.backends.urlopen", return_value=FakeResponse()) as request:
                result = JevBackend().triage(text, config)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(result.backend, "jev")

    def test_yaml_subset_supports_multiline_and_full_width_colon(self) -> None:
        content = '''scenario: demo\nmodel: jev-1.13.0\nclassification:\n  id: category\n  instructions: |\n    主要诉求：请分类\n    仅看文本\n  options:\n    yes: |\n      是：需要处理\n      含中文说明\n    no: 否：无需处理\n'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demo.yaml"
            path.write_text(content, encoding="utf-8")
            config = load_config(path)
        self.assertIn("主要诉求：", config.classification.instructions)
        self.assertIn("是：需要处理", config.classification.options["yes"])
        self.assertEqual(config.classification.options["no"], "否：无需处理")

    def test_mock_provenance_forbids_accuracy_commitment(self) -> None:
        self.assertEqual(report_title({"mock"}), "MOCK 演示数据，无意义")
        self.assertFalse(permits_accuracy_commitment({"mock"}))

    def test_jev_cli_prints_this_run_usage_and_cost(self) -> None:
        config = load_config(ROOT / "configs/ecommerce.yaml")
        fake = TriageResult("jev", "jev-1.13.0", ChoiceResult("positive", 1.0, 1.0, {"positive": 1.0}), {"needs_human_24h": ChoiceResult("no", 1.0, 1.0, {"yes": 0.0, "no": 1.0})}, {"input_tokens": 100, "output_tokens": 9})
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.csv"
            input_path.write_text("text\n只用于离线测试的独特文本\n", encoding="utf-8")
            with patch("jevtriage.cli.ResultCache", return_value=ResultCache(Path(directory) / "cache.sqlite3")), patch("jevtriage.cli.JevBackend.triage", return_value=fake), contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(main(["run", "--config", str(ROOT / "configs/ecommerce.yaml"), "--input", str(input_path), "--backend", "jev", "--yes"]), 0)
        output = stream.getvalue()
        self.assertIn("cumulative_usage_this_run", output)
        self.assertIn("estimated_input_cost_usd", output)

    def test_synthetic_dataset_sizes_and_ambiguity(self) -> None:
        with (ROOT / "data/ecommerce_labeled.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 120)
        self.assertEqual(sum(row["ambiguous"] == "true" for row in rows), 15)
        self.assertTrue(all(row["source"] == "synthetic" for row in rows))
        self.assertEqual(len({row["text"] for row in rows}), 120)
        self.assertTrue(all(row["label"] not in row["text"] for row in rows))
        with (ROOT / "data/sms_labeled.csv").open(encoding="utf-8", newline="") as handle:
            sms = list(csv.DictReader(handle))
        self.assertEqual(len(sms), 60)
        self.assertEqual(len({row["text"] for row in sms}), 60)

    def test_dry_run_stays_offline(self) -> None:
        result = main(["run", "--config", str(ROOT / "configs/ecommerce.yaml"), "--input", str(ROOT / "data/ecommerce_labeled.csv"), "--dry-run", "--limit", "2", "--backend", "jev"])
        self.assertEqual(result, 0)

    def test_stratified_sample_is_balanced_with_at_most_one_ambiguous_per_label(self) -> None:
        with (ROOT / "data/ecommerce_labeled.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        sample = stratified_sample(rows, 10, 42)
        self.assertEqual(len(sample), 10)
        self.assertEqual({row["label"] for row in sample}, {"positive", "negative", "inquiry", "complaint", "other"})
        for label in {row["label"] for row in sample}:
            group = [row for row in sample if row["label"] == label]
            self.assertEqual(len(group), 2)
            self.assertLessEqual(sum(row["ambiguous"] == "true" for row in group), 1)
            self.assertGreaterEqual(sum(row["ambiguous"] == "false" for row in group), 1)

    def test_wilson_feasibility_and_threshold_minimum(self) -> None:
        self.assertEqual([best_case_minimum(target) for target in (.85, .90, .95)], [22, 35, 73])
        records = [{"confidence": .9, "top_probability": .9, "correct": True} for _ in range(34)]
        threshold = fit_threshold(records, "confidence", .90)
        self.assertFalse(threshold.feasible)
        self.assertEqual(threshold.required, 35)

    def test_threshold_failure_messages_distinguish_size_and_errors(self) -> None:
        few = [fit_threshold([{"confidence": .9, "correct": True} for _ in range(5)], "confidence", .90)]
        self.assertEqual(threshold_failure_message(few, .90), "样本量不足，还需要至少 30 条。")
        errors = [fit_threshold([{"confidence": .9, "correct": index < 28} for index in range(30)], "confidence", .85)]
        message = threshold_failure_message(errors, .85)
        self.assertIn("拟合集 30 条中 28 条正确", message)
        self.assertIn("错误数导致无法达标，与样本量无关", message)

    def test_fit_size_limitation_is_data_driven(self) -> None:
        self.assertEqual(fit_size_limitation(60, .85), "本报告将 60 条样本按 1:1 拆分，拟合集为 30 条；目标 0.85 的最低拟合样本门槛为 30 条。")
        self.assertIn("拟合集为 60 条", fit_size_limitation(120, .90))

    def test_confidence_bins_cover_all_records_once(self) -> None:
        records = [{"confidence": value, "correct": True} for value in (0.0, .2, .3, .49, .5, .65, .8, .95, 1.0)]
        bins = confidence_bins(records)
        self.assertEqual(sum(count for _, count, _ in bins), len(records))
        self.assertEqual([label for label, _, _ in bins], ["0.0–0.3", "0.3–0.5", "0.5–0.6", "0.6–0.7", "0.7–0.8", "0.8–0.9", "0.9–1.0", "1.0"])


if __name__ == "__main__":
    unittest.main()

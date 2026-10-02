"""Unit tests for followup_analysis.py parsing and reporting."""
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/bench"))
import followup_analysis


class TestFollowupAnalysis(unittest.TestCase):
    def test_parse_matched_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            trials_dir = tmp_path / "matched" / "matched-rate" / "trials"
            trials_dir.mkdir(parents=True)
            per_run = [
                {
                    "target": "emit-cruby-yjit",
                    "status": "passed",
                    "measurement": {"latency_ms": {"p50": 19.1, "p99": 21.6}},
                    "telemetry": {"summary": {"mean_cpu_pct": 15.5, "peak_container_memory_bytes": 180 * 1024 * 1024}},
                },
                {
                    "target": "emit-cruby-yjit",
                    "status": "passed",
                    "measurement": {"latency_ms": {"p50": 18.9, "p99": 21.4}},
                    "telemetry": {"summary": {"mean_cpu_pct": 15.4, "peak_container_memory_bytes": 182 * 1024 * 1024}},
                },
            ]
            (trials_dir / "per-run.json").write_text(json.dumps(per_run), encoding="utf-8")

            res = followup_analysis.parse_matched_rate(tmp_path)
            self.assertIn("emit-cruby-yjit", res)
            self.assertEqual(res["emit-cruby-yjit"]["completed"], 2)
            self.assertAlmostEqual(res["emit-cruby-yjit"]["p99_median"], 21.6, places=1)

    def test_parse_capacity_cohort(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            trials_dir = tmp_path / "capacity-cruby" / "capacity-cruby" / "trials"
            trials_dir.mkdir(parents=True)
            per_run = [
                {"target": "emit-cruby-yjit", "status": "passed", "capacity_rps": 103.11, "warmup_seconds": 180},
                {"target": "emit-cruby-yjit", "status": "passed", "capacity_rps": 96.86, "warmup_seconds": 210},
                {"target": "rails-cruby-yjit", "status": "failed", "capacity_rps": None, "warmup_seconds": 180},
            ]
            (trials_dir / "per-run.json").write_text(json.dumps(per_run), encoding="utf-8")

            res = followup_analysis.parse_capacity_cohort(tmp_path, "capacity-cruby")
            self.assertEqual(res["emit-cruby-yjit"]["completed"], 2)
            self.assertEqual(res["rails-cruby-yjit"]["completed"], 0)
            self.assertAlmostEqual(res["emit-cruby-yjit"]["capacity_median"], 103.11, places=1)

    def test_parse_spinel_connections(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spinel_dir = tmp_path / "spinel-connections"
            spinel_dir.mkdir(parents=True)
            retest = [{"id": "spinel-r10-pool10", "status": "completed", "exit_code": 0}]
            (spinel_dir / "retest-results.json").write_text(json.dumps(retest), encoding="utf-8")

            cell_trials = spinel_dir / "spinel-r10-pool10" / "trials"
            cell_trials.mkdir(parents=True)
            per_run = [
                {"target": "spinel", "status": "passed", "measurement": {"latency_ms": {"p50": 21.0, "p99": 27.9}, "errors": 0}},
            ]
            (cell_trials / "per-run.json").write_text(json.dumps(per_run), encoding="utf-8")

            res = followup_analysis.parse_spinel_connections(tmp_path)
            self.assertIn("spinel-r10-pool10", res)
            self.assertEqual(res["spinel-r10-pool10"]["completed"], 1)
            self.assertEqual(res["spinel-r10-pool10"]["errors"], 0)

    def test_format_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            text = followup_analysis.format_report(tmp_path)
            self.assertIn("# Follow-up Benchmark Summary", text)


if __name__ == "__main__":
    unittest.main()

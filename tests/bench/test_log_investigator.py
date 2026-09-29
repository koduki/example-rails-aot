"""Checks for artifact loss and the confirmation gate in the agent log skill."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / ".agents/skills/gce-log-investigator/scripts/summarize.py"
SPEC = importlib.util.spec_from_file_location("gce_log_summary", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def measure(failed=0, elapsed=30):
    return dict(requests_total=100, requests_failed=failed, rps_successful=10,
                latency_ms={"p99": 50}, iterations_dropped=0, tester_cpu_pct=10,
                tester_network_errors=0, client_saturated=False, elapsed=elapsed,
                vus_peak=32, rate_offered=10)


class InvestigationTest(unittest.TestCase):
    def test_missing_trial_json_does_not_erase_failed_windows(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            profile = dict(warmup_closed_loop=True, connections=32, measurement_seconds=120,
                           max_error_rate=0.001, slo_p99_ms=100)
            row = dict(target="spinel", repetition=1, status="unstable")
            put(root / "plan.json", dict(profile=profile, schedule=[row]))
            put(root / "env.json", dict(git_commit="test"))
            put(root / "trials/per-run.json", [row])
            trial = root / "trials/0000-spinel"
            put(trial / "warmup.json", [measure(failed=2)])
            put(trial / "warmup-000/k6-summary.json", measure(failed=2))

            report = MODULE.inspect(root)
            self.assertEqual(report["targets"]["spinel"]["warmup_requests_failed"], 2)
            self.assertEqual(report["targets"]["spinel"]["warmup_windows_with_failures"], 1)
            self.assertIn("trial.json", report["trials"][0]["missing"])
            self.assertEqual(report["trials"][0]["warmup_closed_loop"]["configured_vus"], 32)

            put(root / "trials/per-run.json", [dict(row, status="passed", capacity_rps=10)])
            put(trial / "000-confirm-10/k6-summary.json", measure(failed=1, elapsed=120))
            report = MODULE.inspect(root)
            self.assertIsNone(report["trials"][0]["confirmed_capacity_rps"])
            self.assertIn("valid confirmation evidence", report["trials"][0]["missing"])

            put(trial / "000-confirm-10/k6-summary.json", measure(elapsed=120))
            report = MODULE.inspect(root)
            self.assertEqual(report["trials"][0]["confirmed_capacity_rps"], 10)


if __name__ == "__main__":
    unittest.main()

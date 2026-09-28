import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

script_path = Path(__file__).resolve().parents[2] / ".agents/skills/bench-runner-monitor/scripts/check_status.py"
spec = importlib.util.spec_from_file_location("check_status", script_path)
check_status = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_status)


class BenchRunnerMonitorTests(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(check_status.format_duration(0), "0s")
        self.assertEqual(check_status.format_duration(45), "45s")
        self.assertEqual(check_status.format_duration(125), "2m 5s")
        self.assertEqual(check_status.format_duration(3665), "1h 1m 5s")

    def test_render_report_running_state(self):
        data = {
            "mode": "gce",
            "instance": "bench-runner-test",
            "zone": "asia-northeast1-b",
            "project": "test-project",
            "results_dir": "/tmp/bench-results/test",
            "runner_active": True,
            "active_containers": [
                {"name": "rails-bench-abc", "image": "rails-aot-bench:spinel", "status": "Up 2 minutes"}
            ],
            "plan": {
                "total_scheduled": 42,
                "targets": ["spinel"],
                "endpoints": ["/articles"],
                "repetitions": 3,
            },
            "trials": {
                "total_directories": 20,
                "completed_count": 20,
                "status_counts": {"passed": 20},
                "current_trial": "0020-spinel",
                "first_trial_mtime": 1000.0,
                "latest_trial_mtime": 4000.0,
                "recent_completed": [
                    {"name": "0019-spinel", "target": "spinel", "endpoint": "/articles", "status": "passed", "warmup_seconds": 60.1}
                ],
            },
        }
        report = check_status.render_report(data)
        self.assertIn("RUNNING (Active)", report)
        self.assertIn("bench-runner-test", report)
        self.assertIn("20 / 42", report)
        self.assertIn("`passed`: 20", report)
        self.assertIn("rails-bench-abc", report)
        self.assertIn("0019-spinel", report)

    def test_render_report_completed_state(self):
        data = {
            "mode": "local",
            "results_dir": "/tmp/bench-results/done",
            "runner_active": False,
            "plan": {"total_scheduled": 10},
            "trials": {
                "completed_count": 10,
                "status_counts": {"passed": 10},
            },
        }
        report = check_status.render_report(data)
        self.assertIn("COMPLETED", report)
        self.assertIn("10 / 10", report)

    def test_render_report_error_state(self):
        data = {"mode": "gce", "error": "Connection refused"}
        report = check_status.render_report(data)
        self.assertIn("Error inspecting benchmark status", report)
        self.assertIn("Connection refused", report)

    def test_collect_local(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan_file = root / "plan.json"
            plan_file.write_text(json.dumps({
                "profile": {"targets": ["spinel"], "endpoints": ["/articles"], "repetitions": 1},
                "schedule": [{"target": "spinel", "endpoint": "/articles", "repetition": 1}]
            }), encoding="utf-8")

            trial_dir = root / "trials" / "0000-spinel"
            trial_dir.mkdir(parents=True)
            (trial_dir / "trial.json").write_text(json.dumps({
                "target": "spinel",
                "endpoint": "/articles",
                "status": "passed",
                "warmup_seconds": 60.0
            }), encoding="utf-8")

            res = check_status.collect_local(str(root))
            self.assertEqual(res.get("mode"), "local")
            self.assertEqual(res.get("plan", {}).get("total_scheduled"), 1)
            trials = res.get("trials", {})
            self.assertEqual(trials.get("completed_count"), 1)
            self.assertEqual(trials.get("status_counts", {}).get("passed"), 1)
            self.assertEqual(trials.get("latest_completed"), "0000-spinel")


if __name__ == "__main__":
    unittest.main()

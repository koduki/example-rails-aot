#!/usr/bin/env python3
"""Unit tests for reversal analysis and processing stage decomposition engine."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.bench import reversal_analysis


class TestReversalAnalysis(unittest.TestCase):
    def test_calculate_dispersion(self):
        vals = [75.32, 76.06, 77.13, 77.32, 77.49]
        disp = reversal_analysis.calculate_dispersion(vals)
        self.assertEqual(disp["count"], 5)
        self.assertEqual(disp["median"], 77.13)
        self.assertAlmostEqual(disp["mean"], 76.66, places=2)
        self.assertEqual(disp["min"], 75.32)
        self.assertEqual(disp["max"], 77.49)
        self.assertLess(disp["cv_pct"], 2.0)

    def test_calculate_dispersion_empty(self):
        disp = reversal_analysis.calculate_dispersion([])
        self.assertEqual(disp["count"], 0)
        self.assertIsNone(disp["median"])
        self.assertIsNone(disp["mean"])
        self.assertIsNone(disp["cv_pct"])

    def test_evaluate_stage_costs(self):
        eval_3 = reversal_analysis.evaluate_stage_costs(3, 3)
        ops_3 = eval_3["stage_operations"]
        self.assertEqual(ops_3["rails_preloader_hash_ops"], 6)
        self.assertEqual(ops_3["roundhouse_preloader_loop_iterations"], 9)
        self.assertEqual(ops_3["db_paged_loop_reduction_factor"], 1.0)

        eval_1000 = reversal_analysis.evaluate_stage_costs(1000, 1000)
        ops_1000 = eval_1000["stage_operations"]
        self.assertEqual(ops_1000["rails_preloader_hash_ops"], 2000)
        self.assertEqual(ops_1000["roundhouse_preloader_loop_iterations"], 1000000)
        self.assertEqual(ops_1000["db_paged_preloader_loop_iterations"], 400)
        self.assertEqual(ops_1000["db_paged_loop_reduction_factor"], 2500.0)

    def test_analyze_warmup_repetitions_with_c3_data(self):
        c3_trials_dir = Path("bench-results/gce-20260928-c3-capacity/trials")
        if not c3_trials_dir.is_dir():
            self.skipTest("C3 benchmark results directory not present")

        targets = ["rails-cruby-off", "rails-cruby-yjit", "emit-cruby-off", "emit-cruby-yjit"]
        res = reversal_analysis.analyze_warmup_repetitions(c3_trials_dir, targets)

        for tgt in targets:
            self.assertEqual(len(res[tgt]["repetitions"]), 5)
            self.assertIsNotNone(res[tgt]["stats"]["median"])
            self.assertLess(res[tgt]["stats"]["cv_pct"], 5.0)

        # Verify Rails leads over Roundhouse on 1,000 articles
        self.assertGreater(
            res["rails-cruby-off"]["stats"]["median"],
            res["emit-cruby-off"]["stats"]["median"],
        )
        self.assertGreater(
            res["rails-cruby-yjit"]["stats"]["median"],
            res["emit-cruby-yjit"]["stats"]["median"],
        )

    def test_analyze_fixed_100_rps_with_c3_data(self):
        c3_trials_dir = Path("bench-results/gce-20260928-c3-capacity/trials")
        if not c3_trials_dir.is_dir():
            self.skipTest("C3 benchmark results directory not present")

        targets = ["rails-cruby-yjit", "emit-cruby-yjit"]
        res = reversal_analysis.analyze_fixed_100_rps(c3_trials_dir, targets)

        # Rails YJIT 5/5, Emit YJIT 4/5
        self.assertEqual(len(res["rails-cruby-yjit"]), 5)
        self.assertEqual(len(res["emit-cruby-yjit"]), 4)

        # All trials at 100 RPS had 0 errors
        for step in res["rails-cruby-yjit"]:
            self.assertEqual(step["errors"], 0)
            self.assertFalse(step["client_saturated"])
            self.assertGreater(step["peak_container_memory_mib"], 500)

        for step in res["emit-cruby-yjit"]:
            self.assertEqual(step["errors"], 0)
            self.assertFalse(step["client_saturated"])
            self.assertLess(step["peak_container_memory_mib"], 300)

    def test_generate_reversal_report(self):
        warmup = {
            "rails-cruby-off": {
                "repetitions": [{"median_rps": 77.1}],
                "stats": {"median": 77.13, "mean": 76.66, "stdev": 0.94, "cv_pct": 1.22},
            },
            "rails-cruby-yjit": {
                "repetitions": [{"median_rps": 153.1}],
                "stats": {"median": 153.07, "mean": 152.2, "stdev": 2.54, "cv_pct": 1.67},
            },
            "emit-cruby-off": {
                "repetitions": [{"median_rps": 30.5}],
                "stats": {"median": 30.51, "mean": 30.41, "stdev": 0.32, "cv_pct": 1.05},
            },
            "emit-cruby-yjit": {
                "repetitions": [{"median_rps": 114.9}],
                "stats": {"median": 114.94, "mean": 115.06, "stdev": 0.24, "cv_pct": 0.21},
            },
        }
        fixed_100 = {
            "rails-cruby-yjit": [
                {
                    "requests_total": 3000,
                    "requests_successful": 3000,
                    "errors": 0,
                    "client_saturated": False,
                    "latency_p50_ms": 19.2,
                    "latency_p90_ms": 26.7,
                    "latency_p95_ms": 30.4,
                    "latency_p99_ms": 61.5,
                    "mean_cpu_pct": 102.8,
                    "peak_container_memory_mib": 608.2,
                }
            ],
            "emit-cruby-yjit": [
                {
                    "requests_total": 3001,
                    "requests_successful": 3001,
                    "errors": 0,
                    "client_saturated": False,
                    "latency_p50_ms": 27.1,
                    "latency_p90_ms": 37.9,
                    "latency_p95_ms": 43.2,
                    "latency_p99_ms": 55.5,
                    "mean_cpu_pct": 137.9,
                    "peak_container_memory_mib": 217.5,
                }
            ],
        }

        md = reversal_analysis.generate_reversal_report(warmup, fixed_100)
        self.assertIn("Rails vs Roundhouse Performance Reversal Diagnostic", md)
        self.assertIn("peak_container_memory_bytes", md)
        self.assertIn("No stage timings", md)
        self.assertIn("61.5", md)
        self.assertNotIn("32.8 ms", md)
        self.assertNotIn("confirmed an active", md)



if __name__ == "__main__":
    unittest.main()

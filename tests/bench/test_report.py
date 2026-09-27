#!/usr/bin/env python3
"""Unit tests for statistical report generator and pairwise comparisons."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import report

class ReportUnitTests(unittest.TestCase):
    def test_summarize_series(self):
        self.assertEqual(report.summarize_series([]), {'median': None, 'min': None, 'max': None, 'iqr': None})
        res = report.summarize_series([10, 20, 30, 40, 50])
        self.assertEqual(res['median'], 30.0)
        self.assertEqual(res['min'], 10.0)
        self.assertEqual(res['max'], 50.0)
        self.assertEqual(res['iqr'], 20.0)

    def test_compute_ratio_handles_missing_and_zero(self):
        self.assertIsNone(report.compute_ratio(None, 100))
        self.assertIsNone(report.compute_ratio(100, None))
        self.assertIsNone(report.compute_ratio(100, 0))
        self.assertIsNone(report.compute_ratio(100, -10))
        self.assertEqual(report.compute_ratio(250, 100), 2.5)

    def test_pairwise_ratios(self):
        aggregates = [
            {'target': 'rails-cruby-off', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 100.0}},
            {'target': 'rails-cruby-yjit', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 150.0}},
            {'target': 'emit-cruby-off', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 120.0}},
            {'target': 'emit-cruby-yjit', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 216.0}},
            {'target': 'rails-jruby', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 110.0}},
            {'target': 'rails-jruby-off', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 80.0}},
            {'target': 'emit-jruby', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 165.0}},
            {'target': 'emit-jruby-off', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 100.0}},
            {'target': 'spinel', 'endpoint': '/articles', 'is_eligible': True, 'valid_repetition_count': 3, 'rps': {'median': 300.0}},
        ]

        pw = report.compute_pairwise_comparisons(aggregates, '/articles')

        # Roundhouse speedup: emit / rails
        self.assertEqual(pw['roundhouse_speedup']['cruby_off'], 1.2)   # 120 / 100
        self.assertEqual(pw['roundhouse_speedup']['cruby_yjit'], 1.44) # 216 / 150
        self.assertEqual(pw['roundhouse_speedup']['jruby_jit'], 1.5)   # 165 / 110

        # YJIT speedup G = ON / OFF
        self.assertEqual(pw['yjit_speedup_g']['rails'], 1.5)           # 150 / 100
        self.assertEqual(pw['yjit_speedup_g']['emitted'], 1.8)         # 216 / 120

        # Interaction ratio: G_emitted / G_rails
        self.assertEqual(pw['yjit_speedup_g']['interaction_ratio'], 1.2) # 1.8 / 1.5

        # JRuby compile mode speedup: JIT / OFF
        self.assertEqual(pw['jruby_compile_mode_ratio']['rails'], 1.375)  # 110 / 80
        self.assertEqual(pw['jruby_compile_mode_ratio']['emitted'], 1.65) # 165 / 100

        # Spinel system comparison
        self.assertEqual(pw['spinel_system_comparison']['ratio_vs_rails_cruby_off'], 3.0)  # 300 / 100
        self.assertEqual(pw['spinel_system_comparison']['ratio_vs_rails_cruby_yjit'], 2.0) # 300 / 150

    def test_client_saturation_and_ineligible_trials_are_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            preflight = {
                'rails-cruby-off': {'eligible_endpoints': ['/articles']},
                'emit-cruby-off': {'eligible_endpoints': []},
            }
            (root / 'preflight').mkdir()
            (root / 'preflight/preflight.json').write_text(json.dumps(preflight))

            trials = [
                # Valid trial
                {'target': 'rails-cruby-off', 'endpoint': '/articles', 'repetition': 1, 'status': 'passed',
                 'measurement': {'rps_successful': 100.0, 'latency_ms': {'med': 5.0, 'p95': 10.0, 'p99': 15.0}, 'requests_total': 1000, 'requests_failed': 0}},
                # Client saturated trial (dropped iterations)
                {'target': 'rails-cruby-off', 'endpoint': '/articles', 'repetition': 2, 'status': 'passed',
                 'measurement': {'rps_successful': 150.0, 'iterations_dropped': 5, 'client_saturated': True, 'latency_ms': {'med': 5.0, 'p95': 10.0, 'p99': 15.0}}},
                # Ineligible endpoint trial
                {'target': 'emit-cruby-off', 'endpoint': '/articles', 'repetition': 1, 'status': 'passed',
                 'measurement': {'rps_successful': 200.0, 'latency_ms': {'med': 2.0, 'p95': 4.0, 'p99': 6.0}}},
            ]
            (root / 'trials').mkdir()
            (root / 'trials/per-run.json').write_text(json.dumps(trials))

            data = report.build_report(root)

            # Check rails-cruby-off has only 1 valid repetition (rep 2 dropped due to client saturation)
            r_cruby = next(a for a in data['target_aggregates'] if a['target'] == 'rails-cruby-off')
            self.assertEqual(r_cruby['valid_repetition_count'], 1)
            self.assertEqual(r_cruby['excluded_repetition_count'], 1)
            self.assertIn('Client saturated', r_cruby['excluded_reasons'][0]['reason'])

            # Check emit-cruby-off is ineligible
            e_cruby = next(a for a in data['target_aggregates'] if a['target'] == 'emit-cruby-off')
            self.assertFalse(e_cruby['is_eligible'])
            self.assertEqual(e_cruby['valid_repetition_count'], 0)

            # Artifact files created
            self.assertTrue((root / 'summary.json').exists())
            self.assertTrue((root / 'summary.md').exists())
            self.assertTrue((root / 'summary.csv').exists())

            md = (root / 'summary.md').read_text(encoding='utf-8')
            self.assertIn('# Benchmark P1 Pairwise Comparison Report', md)
            self.assertIn('`rails-cruby-off`', md)
            self.assertIn('## 3. Individual Trial Dispositions and Execution Details', md)
            self.assertIn('Client saturated', md)
            self.assertIn('trials', data)
            self.assertEqual(len(data['trials']), 3)

    def test_fixed_offered_rate_does_not_publish_capacity_ratios(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'preflight').mkdir()
            (root / 'trials').mkdir()
            (root / 'plan.json').write_text(json.dumps({'profile': {'driver': 'k6', 'offered_rps': 50}}))
            (root / 'preflight/preflight.json').write_text(json.dumps({
                name: {'eligible_endpoints': ['/articles']} for name in ('rails-cruby-off', 'emit-cruby-off')
            }))
            (root / 'trials/per-run.json').write_text(json.dumps([
                {'target': name, 'endpoint': '/articles', 'repetition': 1, 'status': 'passed',
                 'measurement': {'rps_successful': 50, 'latency_ms': {'p99': 4}, 'requests_total': 500,
                                 'requests_failed': 0}}
                for name in ('rails-cruby-off', 'emit-cruby-off')
            ]))
            data = report.build_report(root)
            self.assertTrue(data['fixed_offered_rate'])
            self.assertEqual(data['pairwise_comparisons'], [])
            self.assertIn('does not establish maximum capacity', (root / 'summary.md').read_text(encoding='utf-8'))

if __name__ == '__main__':
    unittest.main()

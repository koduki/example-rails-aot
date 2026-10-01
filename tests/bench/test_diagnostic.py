#!/usr/bin/env python3
"""Unit tests for Issue #18 JRuby ON/OFF and CRuby YJIT diagnostic pipeline."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))

import diagnostic
import run

class DiagnosticUnitTests(unittest.TestCase):
    def add_capacity_evidence(self, grouped):
        for target, records in grouped.items():
            for record in records:
                t = record['trial']; m = t['measurement']
                t.update(target=target, repetition=1, capacity_rps=m['rps'])
                m.update(driver='k6-open-arrival', elapsed=120, rate_offered=m['rps'],
                         rps_successful=m['rps'], requests_total=120*m['rps'], requests_failed=0)

    def test_diagnostic_profile_validity(self):
        profile_path = ROOT / 'bench/profiles/diagnostic.yml'
        self.assertTrue(profile_path.exists())
        p = run.config(profile_path)
        self.assertTrue(p.get('diagnostics'))
        self.assertTrue(p.get('allow_unstable'))
        self.assertEqual(len(p['targets']), 8)
        # Verify 2x2 JRuby and 2x2 CRuby targets are present
        expected_targets = {
            'rails-cruby-off', 'rails-cruby-yjit',
            'emit-cruby-off', 'emit-cruby-yjit',
            'rails-jruby-off', 'rails-jruby',
            'emit-jruby-off', 'emit-jruby'
        }
        self.assertEqual(set(p['targets']), expected_targets)
        sched = run.schedule(p)
        self.assertEqual(len(sched), 8 * len(p['endpoints']) * p['repetitions'])

    def test_warmup_trajectory_analysis(self):
        windows = [
            {'elapsed': 5.0, 'rps': 100.0, 'rps_successful': 100.0, 'p95_ms': 20.0, 'latency_ms': {'p95': 20.0}},
            {'elapsed': 5.0, 'rps': 150.0, 'rps_successful': 150.0, 'p95_ms': 15.0, 'latency_ms': {'p95': 15.0}},
            {'elapsed': 5.0, 'rps': 180.0, 'rps_successful': 180.0, 'p95_ms': 12.0, 'latency_ms': {'p95': 12.0}},
            {'elapsed': 5.0, 'rps': 182.0, 'rps_successful': 182.0, 'p95_ms': 11.8, 'latency_ms': {'p95': 11.8}},
            {'elapsed': 5.0, 'rps': 181.0, 'rps_successful': 181.0, 'p95_ms': 11.9, 'latency_ms': {'p95': 11.9}},
        ]
        p = {'stable_windows': 3, 'max_cv': 0.05, 'max_drift': 0.05, 'warmup_min_seconds': 15}
        res = diagnostic.analyze_warmup_trajectory(windows, p)
        self.assertEqual(res['window_count'], 5)
        self.assertEqual(res['total_warmup_seconds'], 25.0)
        self.assertEqual(res['initial_rps'], 100.0)
        self.assertEqual(res['final_rps'], 181.0)
        self.assertEqual(res['rps_growth_pct'], 81.0)
        self.assertEqual(res['initial_p95_ms'], 20.0)
        self.assertEqual(res['final_p95_ms'], 11.9)
        self.assertTrue(res['stabilized'])
        self.assertIsNotNone(res['stabilized_at_seconds'])
        self.assertLess(res['final_cv'], 0.05)

    def test_jruby_matrix_and_interaction(self):
        trials_by_target = {
            'rails-jruby-off': [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                           'measurement': {'rps': 50.0, 'latency_ms': {'p95': 25.0, 'p99': 35.0}}},
                                 'diagnostics': {'end': {'compile_mode': 'OFF', 'jvm_compiler': 'OpenJDK 64-Bit Server VM',
                                                         'jvm_compilation_time_ms': 1200, 'jvm_args': ['-Xcompile.mode=OFF']}}}],
            'rails-jruby':     [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                           'measurement': {'rps': 100.0, 'latency_ms': {'p95': 15.0, 'p99': 20.0}}},
                                 'diagnostics': {'end': {'compile_mode': 'JIT', 'jvm_compiler': 'OpenJDK 64-Bit Server VM',
                                                         'jvm_compilation_time_ms': 3500, 'jvm_args': ['-Xcompile.mode=JIT']}}}],
            'emit-jruby-off':  [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                           'measurement': {'rps': 200.0, 'latency_ms': {'p95': 8.0, 'p99': 12.0}}},
                                 'diagnostics': {'end': {'compile_mode': 'OFF', 'jvm_compiler': 'OpenJDK 64-Bit Server VM',
                                                         'jvm_compilation_time_ms': 1100, 'jvm_args': ['-Xcompile.mode=OFF']}}}],
            'emit-jruby':      [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                           'measurement': {'rps': 500.0, 'latency_ms': {'p95': 4.0, 'p99': 6.0}}},
                                 'diagnostics': {'end': {'compile_mode': 'JIT', 'jvm_compiler': 'OpenJDK 64-Bit Server VM',
                                                         'jvm_compilation_time_ms': 3800, 'jvm_args': ['-Xcompile.mode=JIT']}}}],
        }
        self.add_capacity_evidence(trials_by_target)
        matrix = diagnostic.analyze_jruby_matrix(trials_by_target, '/articles')
        self.assertEqual(matrix['endpoint'], '/articles')
        # G_Rails = 100 / 50 = 2.0
        self.assertEqual(matrix['jruby_speedup_g']['rails'], 2.0)
        # G_emitted = 500 / 200 = 2.5
        self.assertEqual(matrix['jruby_speedup_g']['emitted'], 2.5)
        # Interaction = 2.5 / 2.0 = 1.25
        self.assertEqual(matrix['jruby_speedup_g']['interaction_ratio'], 1.25)
        # Roundhouse speedup with JIT OFF = 200 / 50 = 4.0
        self.assertEqual(matrix['roundhouse_speedup']['jruby_compile_off'], 4.0)
        # Roundhouse speedup with JIT ON = 500 / 100 = 5.0
        self.assertEqual(matrix['roundhouse_speedup']['jruby_compile_jit'], 5.0)
        # Verification evidence
        self.assertTrue(matrix['jvm_jit_verified_active_across_all'])

    def test_cruby_yjit_matrix_and_interaction(self):
        trials_by_target = {
            'rails-cruby-off': [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                           'measurement': {'rps': 60.0, 'latency_ms': {'p95': 20.0, 'p99': 30.0}}},
                                 'diagnostics': {'end': {'yjit_enabled': False, 'gc_stat': {'total_allocated_objects': 100000}}}}],
            'rails-cruby-yjit': [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                            'measurement': {'rps': 90.0, 'latency_ms': {'p95': 14.0, 'p99': 22.0}}},
                                  'diagnostics': {'end': {'yjit_enabled': True, 'yjit_stats': {'compiled_block_count': 1500, 'ratio_in_yjit': 0.85},
                                                          'gc_stat': {'total_allocated_objects': 98000}}}}],
            'emit-cruby-off':  [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                           'measurement': {'rps': 300.0, 'latency_ms': {'p95': 5.0, 'p99': 8.0}}},
                                 'diagnostics': {'end': {'yjit_enabled': False, 'gc_stat': {'total_allocated_objects': 40000}}}}],
            'emit-cruby-yjit': [{'trial': {'endpoint': '/articles', 'status': 'passed',
                                           'measurement': {'rps': 600.0, 'latency_ms': {'p95': 3.0, 'p99': 4.5}}},
                                 'diagnostics': {'end': {'yjit_enabled': True, 'yjit_stats': {'compiled_block_count': 600, 'ratio_in_yjit': 0.92},
                                                         'gc_stat': {'total_allocated_objects': 39000}}}}],
        }
        self.add_capacity_evidence(trials_by_target)
        matrix = diagnostic.analyze_cruby_matrix(trials_by_target, '/articles')
        # G_Rails = 90 / 60 = 1.5
        self.assertEqual(matrix['yjit_speedup_g']['rails'], 1.5)
        # G_emitted = 600 / 300 = 2.0
        self.assertEqual(matrix['yjit_speedup_g']['emitted'], 2.0)
        # Interaction = 2.0 / 1.5 = 1.333
        self.assertAlmostEqual(matrix['yjit_speedup_g']['interaction_ratio'], 4/3)
        # Roundhouse speedup with YJIT OFF = 300 / 60 = 5.0
        self.assertEqual(matrix['roundhouse_speedup']['yjit_off'], 5.0)
        # Roundhouse speedup with YJIT ON = 600 / 90 = 6.667
        self.assertAlmostEqual(matrix['roundhouse_speedup']['yjit_on'], 20/3)

    def test_missing_diagnostics_handled_gracefully(self):
        trials_by_target = {
            'rails-jruby-off': [{'trial': {'endpoint': '/articles', 'status': 'failed', 'reason': 'Crashed'}}],
        }
        matrix = diagnostic.analyze_jruby_matrix(trials_by_target, '/articles')
        self.assertIsNone(matrix['jruby_speedup_g']['rails'])
        self.assertIsNone(matrix['jruby_speedup_g']['emitted'])
        self.assertFalse(matrix['jvm_jit_verified_active_across_all'])

    def test_build_diagnostic_report_e2e(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            preflight = {'rails-cruby-off': {'eligible_endpoints': ['/articles']}}
            (root / 'preflight.json').write_text(json.dumps(preflight), encoding='utf-8')
            plan = {'profile': {'endpoints': ['/articles'], 'diagnostics': True, 'stable_windows': 3}}
            (root / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')

            trials_dir = root / 'trials'
            trials_dir.mkdir()
            per_run = [{'target': 'rails-cruby-off', 'endpoint': '/articles', 'repetition': 1, 'status': 'passed'}]
            (root / 'per-run.json').write_text(json.dumps(per_run), encoding='utf-8')

            t_dir = trials_dir / '0000-rails-cruby-off'
            t_dir.mkdir()
            (t_dir / 'trial.json').write_text(json.dumps({
                'target': 'rails-cruby-off', 'endpoint': '/articles', 'repetition': 1, 'status': 'passed',
                'measurement': {'rps': 50.0, 'latency_ms': {'p95': 20.0, 'p99': 30.0}}
            }), encoding='utf-8')
            (t_dir / 'warmup.json').write_text(json.dumps([
                {'elapsed': 5.0, 'rps': 40.0, 'p95_ms': 25.0},
                {'elapsed': 5.0, 'rps': 50.0, 'p95_ms': 20.0},
                {'elapsed': 5.0, 'rps': 50.0, 'p95_ms': 20.0},
            ]), encoding='utf-8')
            (t_dir / 'diagnostics.json').write_text(json.dumps({
                'end': {'yjit_enabled': False, 'gc_stat': {'total_allocated_objects': 10000}}
            }), encoding='utf-8')

            report_data = diagnostic.build_diagnostic_report(root)
            self.assertTrue((root / 'diagnostics.json').exists())
            self.assertTrue((root / 'diagnostics.md').exists())

            md = (root / 'diagnostics.md').read_text(encoding='utf-8')
            self.assertIn('Instrumentation Overhead Active', md)
            self.assertIn('JRuby compile.mode=OFF vs JVM JIT', md)
            self.assertIn('Epistemological Classification', md)
            self.assertIn('実測事実', md)
            self.assertIn('説明を支持する観測', md)
            self.assertIn('未検証の仮説', md)
            self.assertIn('Runtime Failure Diagnosis and Viable Load Envelopes', md)

    def test_failure_taxonomy_classification(self):
        windows = [
            {'requests_total': 100, 'requests_failed': 90, 'errors': 90, 'failure_counts': {'timeout': 90}, 'latency_ms': {'max': 5001.0, 'p99': 5000.5}},
            {'requests_total': 100, 'requests_failed': 0, 'errors': 0, 'latency_ms': {'max': 100.0, 'p99': 50.0}},
            {'requests_total': 100, 'requests_failed': 10, 'errors': 10, 'failure_counts': {'http_status': 10}, 'latency_ms': {'max': 200.0, 'p99': 150.0}},
        ]
        tax = diagnostic.analyze_failure_taxonomy(windows)
        self.assertEqual(tax['total_windows'], 3)
        self.assertEqual(tax['failed_windows'], 2)
        self.assertEqual(tax['failed_windows_pct'], 66.67)
        self.assertEqual(tax['total_requests'], 300)
        self.assertEqual(tax['failed_requests'], 100)
        self.assertEqual(tax['error_rate_pct'], 33.333)
        self.assertEqual(tax['timeout_failures'], 90)
        self.assertEqual(tax['http_failures'], 10)

    def test_concurrency_and_queuing_jruby_off(self):
        windows = [
            {'elapsed': 30.0, 'requests_total': 204, 'requests_successful': 12, 'requests_failed': 192,
             'rps_successful': 0.4, 'vus_peak': 32, 'latency_ms': {'min': 1447.0, 'med': 5000.0, 'p95': 5000.8, 'p99': 5001.1}}
        ]
        cpu_start = {'cpu.stat': 'usage_usec 0\nuser_usec 0\nsystem_usec 0\nnr_throttled 0'}
        cpu_end = {'cpu.stat': 'usage_usec 120000000\nuser_usec 119880000\nsystem_usec 120000\nnr_throttled 0'}
        q = diagnostic.analyze_concurrency_and_queuing(windows, cpu_start, cpu_end, num_threads=4, target_name='emit-jruby-off')
        self.assertEqual(q['concurrency_vus'], 32)
        self.assertEqual(q['threads'], 4)
        self.assertIsNone(q['estimated_service_time_ms'])
        self.assertIsNone(q['max_theoretical_throughput_rps'])
        self.assertIsNone(q['estimated_queue_depth'])
        self.assertIsNone(q['estimated_queue_delay_ms'])
        self.assertEqual(q['observed']['http_min_ms'], 1447.0)
        self.assertEqual(q['root_cause'], 'unresolved')
        self.assertIsNone(q['cpu_metrics']['vcpus_active'])  # Historic endpoints lack timestamps.
        self.assertAlmostEqual(q['cpu_metrics']['pct_user'], 99.9, places=1)

    def test_names_and_http_minimum_do_not_create_safe_envelopes(self):
        for name, latency in [('emit-jruby-off', 1447), ('spinel', 15)]:
            env = diagnostic.compute_viable_load_envelope(name, latency)
            self.assertEqual(env['status'], 'unconfirmed')
            self.assertIsNone(env['slo_achievable'])
            self.assertIsNone(env['safe_open_arrival_rps'])
            self.assertIsNone(env['max_viable_concurrency_vus_no_timeout'])

    def test_historic_latency_does_not_classify_timeouts(self):
        tax = diagnostic.analyze_failure_taxonomy([
            {'requests_total': 100, 'requests_failed': 90, 'latency_ms': {'p99': 5001}}])
        self.assertEqual(tax['unknown_failures'], 90)
        self.assertEqual(tax['timeout_failures'], 0)

    def test_unpaired_or_fixed_rate_observations_have_no_speedup(self):
        grouped = {'rails-cruby-off': [{'trial': {'endpoint': '/articles', 'repetition': 1,
                    'status': 'passed', 'measurement': {'rps': 100}}}]}
        matrix = diagnostic.analyze_cruby_matrix(grouped, '/articles')
        self.assertIsNone(matrix['yjit_speedup_g']['rails'])


if __name__ == '__main__':
    unittest.main()

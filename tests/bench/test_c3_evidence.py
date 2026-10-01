"""Regression checks for boundary refinement, failed evidence and diagnostic planning."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import capacity
import diagnostic
import load_sweep
import run
import run_gce_suite


def measurement(rate, p99=20):
    return {'driver': 'k6-open-arrival', 'elapsed': 120, 'requests_total': rate * 120,
            'requests_failed': 0, 'rate_offered': rate, 'rps_successful': rate,
            'latency_ms': {'p99': p99}, 'iterations_dropped': 0}


class C3EvidenceTests(unittest.TestCase):
    def profile(self):
        return run.config(ROOT / 'bench/profiles/gce-c3-capacity.yml')

    def test_failed_125_second_confirmation_refines_back_above_62(self):
        p = self.profile(); p.pop('remote_loadgen', None)
        p.update(capacity_start_rps=100, capacity_max_rps=200, capacity_tolerance_rps=25,
                 capacity_tolerance_ratio=None)
        def measure(rate, seconds, phase):
            return measurement(rate, 150 if rate >= 150 or (phase == 'confirm' and rate > 100) else 20)
        result = capacity.search(measure, p)
        self.assertGreaterEqual(result['confirmed_lower_rps'], 93)
        self.assertLessEqual(result['failed_upper_rps'] - result['confirmed_lower_rps'], 25)
        self.assertTrue(any(s['phase'] == 'confirm' and s['rate'] > 62.5 and s['decision'] == 'pass'
                            for s in result['steps']))

    def test_sub_25_capacity_uses_fractional_refinement(self):
        p = self.profile(); p.pop('remote_loadgen', None)
        p.update(capacity_start_rps=25, capacity_max_rps=100)
        result = capacity.search(lambda rate, *_: measurement(rate, 120 if rate > 10 else 20), p)
        self.assertEqual(result['status'], 'pass')
        self.assertGreater(result['confirmed_lower_rps'], 9.5)
        self.assertLessEqual(result['resolution_rps'], result['tolerance_rps'])
        self.assertEqual(result['steps'][-1]['rate'], result['confirmed_lower_rps'])

    def test_invalid_missing_latency_and_budget_retain_steps(self):
        p = self.profile(); p.pop('remote_loadgen', None)
        self.assertEqual(capacity.decision(dict(measurement(25), latency_ms={}), p), 'unclassified')
        self.assertEqual(capacity.decision({'transport_error': True}, p), 'transport_error')
        p['capacity_max_steps'] = 1
        with self.assertRaisesRegex(RuntimeError, 'budget exhausted') as caught:
            capacity.search(lambda rate, *_: measurement(rate), p)
        self.assertEqual(len(caught.exception.steps), 1)

    def test_final_boundary_reversal_does_not_claim_capacity(self):
        p = self.profile(); p.pop('remote_loadgen', None)
        p.update(capacity_start_rps=25, capacity_max_rps=50, capacity_tolerance_rps=5,
                 capacity_tolerance_ratio=None)
        counts = {}
        def measure(rate, seconds, phase):
            key = (rate, phase); counts[key] = counts.get(key, 0) + 1
            return measurement(rate, 150 if rate > 35 or (phase == 'confirm' and rate > 30) or counts[key] > 1 else 20)
        result = capacity.search(measure, p)
        self.assertEqual(result['status'], 'boundary_unstable')
        self.assertIsNone(result['capacity_rps'])

    def test_trial_json_saved_on_server_failure_and_interrupt(self):
        p = run.config(ROOT / 'bench/profiles/quick.yml')
        p.update(targets=['rails-cruby-off'], endpoints=['/articles'], repetitions=1)
        for error, status in [(RuntimeError('start failed'), 'failed'), (KeyboardInterrupt(), 'interrupted')]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                server = MagicMock(); server.__enter__.side_effect = error
                with patch.object(run, 'Server', return_value=server):
                    if status == 'interrupted':
                        with self.assertRaises(KeyboardInterrupt):
                            run.trials(p, {}, Path(tmp), {'rails-cruby-off': {'eligible_endpoints': ['/articles']}})
                    else:
                        run.trials(p, {}, Path(tmp), {'rails-cruby-off': {'eligible_endpoints': ['/articles']}})
                local = json.loads((Path(tmp) / '0000-rails-cruby-off/trial.json').read_text())
                index = json.loads((Path(tmp) / 'per-run.json').read_text())
                self.assertEqual(local['status'], status)
                self.assertEqual(local, index[0])
                self.assertIn('finished_at', local)

    def test_recovery_probe_is_bounded_and_does_not_certify_queue_drain(self):
        p = self.profile(); p.pop('remote_loadgen', None)
        p['recovery_health_p99_ms'] = 100
        with tempfile.TemporaryDirectory() as tmp, patch.object(run, 'sample', side_effect=[measurement(1, 200), measurement(1)]):
            run.recover(MagicMock(), '/articles', p, {}, Path(tmp))
            evidence = json.loads((Path(tmp) / 'recovery.json').read_text())
            self.assertEqual(evidence['status'], 'healthy_probe')
            self.assertIsNone(evidence['server_queue_drained'])
        with tempfile.TemporaryDirectory() as tmp, patch.object(run, 'sample', return_value=measurement(1, 200)) as sample:
            with self.assertRaises(RuntimeError):
                run.recover(MagicMock(), '/articles', p, {}, Path(tmp))
            self.assertEqual(sample.call_count, p['recovery_max_attempts'])

    def test_received_manifest_mismatch_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'plan.json').write_text('{}')
            run_gce_suite.verify_artifact_checksums(root)
            before = (root / 'SHA256SUMS').read_bytes()
            (root / 'plan.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                run_gce_suite.verify_artifact_checksums(root)
            self.assertEqual((root / 'SHA256SUMS').read_bytes(), before)

    def test_historic_timeout_log_is_evidence_without_latency_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); trial = root / 'trials/0000-spinel'
            run.save(trial / 'trial.json', {'target': 'spinel', 'repetition': 1})
            run.save(trial / 'warmup.json', [{'requests_total': 10, 'requests_failed': 1,
                                           'latency_ms': {'p99': 20}}])
            log = trial / 'warmup-000/k6.log'; log.parent.mkdir()
            log.write_text('msg="Request Failed" error="Get url: request timeout"\n')
            _, _, details, _ = diagnostic.parse_diagnostic_artifacts(root)
            tax = diagnostic.analyze_failure_taxonomy(details[0]['warmup'])
            self.assertEqual(tax['timeout_failures'], 1)
            self.assertEqual(tax['unknown_failures'], 0)
            self.assertIn('failure_counts_evidence', details[0]['warmup'][0])
            log.write_text(log.read_text() * 2)
            _, _, details, _ = diagnostic.parse_diagnostic_artifacts(root)
            self.assertEqual(diagnostic.analyze_failure_taxonomy(details[0]['warmup'])['unknown_failures'], 1)
            self.assertIn('failure-count mismatch', details[0]['missing'][0])

    def test_cpu_unavailable_remains_missing_and_trial_timestamps_bound_cpu(self):
        q = diagnostic.analyze_concurrency_and_queuing([], {'cpu.stat': {'status': 'unavailable'}},
                                                       {'cpu.stat': {'status': 'unavailable'}})
        self.assertEqual(q['cpu_metrics'], {})
        q = diagnostic.analyze_concurrency_and_queuing([], {'cpu.stat': 'usage_usec 0', 'timestamp': 10},
                                                       {'cpu.stat': 'usage_usec 4000000', 'timestamp': 12})
        self.assertEqual(q['cpu_metrics']['vcpus_active'], 2)

    def test_confirmed_points_use_actual_profile_slo_and_issuance(self):
        m = measurement(10, 80)
        env = diagnostic.compute_viable_load_envelope('spinel', observations=[m], slo_p99_ms=50)
        self.assertEqual(env['status'], 'unconfirmed')
        env = diagnostic.compute_viable_load_envelope('spinel', observations=[m])
        self.assertEqual(env['confirmed_points'][0]['offered_rps'], 10)
        self.assertIsNone(env['safe_open_arrival_rps'])

    def test_sweep_cells_preserve_modes_and_instrumentation_separation(self):
        p = run.config(ROOT / 'bench/profiles/diagnostic-load-sweep.yml')
        cells = load_sweep.plan(p, 'vus', [1, 4, 16])
        self.assertEqual([c['profile']['connections'] for c in cells], [1, 4, 16])
        self.assertTrue(all(c['profile']['measurement_closed_loop'] for c in cells))
        self.assertTrue(all(not c['profile']['diagnostics'] for c in cells))
        self.assertTrue(all(c['profile']['warmup_connections'] == 1 for c in cells))
        self.assertTrue(load_sweep.plan(p, 'rates', [0.5], instrumented=True)[0]['profile']['diagnostics'])
        with self.assertRaises(ValueError):
            load_sweep.plan(p, 'vus', [1.5])


if __name__ == '__main__':
    unittest.main()

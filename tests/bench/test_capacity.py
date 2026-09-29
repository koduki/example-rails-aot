import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import capacity
import gce_report
from loadgen import RemoteLoadGenerator, remote_command, remote_copy, network_errors
import run


class CapacityTests(unittest.TestCase):
    profile = {'capacity_start_rps': 100, 'capacity_max_rps': 800,
               'capacity_tolerance_rps': 25, 'capacity_step_seconds': 3,
               'measurement_seconds': 120, 'max_error_rate': 0.001,
               'slo_p99_ms': 100}

    def result(self, rate, p99=20, **changes):
        m = {'requests_total': rate * 3, 'requests_failed': 0,
             'iterations_dropped': 0, 'client_saturated': False,
             'latency_ms': {'p99': p99}, 'rate_offered': rate,
             'rps_successful': rate}
        m.update(changes)
        return m

    def test_bracket_and_sustained_confirmation(self):
        steps = []
        def measure(rate, duration, phase):
            steps.append((rate, duration, phase))
            return self.result(rate, p99=120 if rate > 350 else 20)
        result = capacity.search(measure, self.profile)
        self.assertEqual(result['status'], 'pass')
        self.assertTrue(325 <= result['capacity_rps'] <= 350)
        self.assertEqual(steps[-1][1:], (120, 'confirm'))
        self.assertTrue(any(s['decision'] == 'slo_fail' for s in result['steps']))

    def test_client_saturation_never_becomes_server_capacity(self):
        with self.assertRaisesRegex(RuntimeError, 'Client saturation'):
            capacity.search(lambda rate, *_: self.result(rate, iterations_dropped=1), self.profile)
        self.assertEqual(capacity.decision(self.result(100, rps_successful=50), self.profile), 'invalid_client')

    def test_search_does_not_claim_maximum_without_upper_bound(self):
        result = capacity.search(lambda rate, *_: self.result(rate), self.profile)
        self.assertEqual(result['status'], 'upper_bound_not_found')
        self.assertIsNone(result['capacity_rps'])
        self.assertEqual(result['offered_rps'], self.profile['capacity_max_rps'])

    def test_confirmation_failure_invalidates_short_pass(self):
        outcome = capacity.search(lambda rate, duration, phase: self.result(rate, p99=150 if phase == 'confirm' or rate >= 200 else 20), self.profile)
        self.assertIsNone(outcome['capacity_rps'])

    def test_downward_search_recovers_when_start_rate_exceeds_slo(self):
        profile = dict(self.profile, capacity_start_rps=100, capacity_min_rps=25, capacity_tolerance_rps=25)
        def measure(rate, duration, phase):
            return self.result(rate, p99=150 if rate > 25 else 20)
        result = capacity.search(measure, profile)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['capacity_rps'], 25)
        phases = [s['phase'] for s in result['steps']]
        self.assertIn('downward', phases)
        self.assertEqual(phases[-1], 'confirm')

    def test_downward_search_recovers_when_start_rate_causes_client_saturation(self):
        profile = dict(self.profile, capacity_start_rps=100, capacity_min_rps=25, capacity_tolerance_rps=25)
        def measure(rate, duration, phase):
            if rate == 100:
                return self.result(rate, client_saturated=True, iterations_dropped=10)
            return self.result(rate, p99=120 if rate >= 50 else 20)
        result = capacity.search(measure, profile)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['capacity_rps'], 25)

    def test_downward_search_fails_when_even_min_rps_fails(self):
        profile = dict(self.profile, capacity_start_rps=100, capacity_min_rps=25)
        def measure(rate, duration, phase):
            return self.result(rate, p99=500)
        with self.assertRaisesRegex(RuntimeError, 'No sustainable starting rate'):
            capacity.search(measure, profile)

    def test_paired_repetitions_exclude_incomplete(self):
        rows = [{'target': t, 'repetition': rep, 'status': 'passed', 'capacity_rps': val}
                for rep, (a, b) in enumerate(((200, 100), (150, 100), (400, 200)), 1)
                for t, val in (('emit-cruby-off', a), ('rails-cruby-off', b))]
        rows[-1]['status'] = 'failed'
        pairs = capacity.paired_ratios(rows, 'emit-cruby-off', 'rails-cruby-off')
        self.assertEqual([p['ratio'] for p in pairs['pairs']], [2, 1.5])
        self.assertEqual(pairs['median'], 1.75)

    def test_formal_report_uses_all_repetitions_and_worst_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'trials').mkdir()
            (root / 'preflight').mkdir()
            profile = dict(self.profile, capacity_search=True, targets=['rails-cruby-off'],
                           endpoints=['/articles?page=1'], fixture_articles=1000,
                           repetitions=5, gce_zone='zone', remote_loadgen='tester', target_host='10.0.0.1')
            (root / 'plan.json').write_text(json.dumps({'profile': profile}))
            (root / 'env.json').write_text(json.dumps({'git_commit': 'source-sha'}))
            (root / 'preflight/preflight.json').write_text(json.dumps({
                'rails-cruby-off': {'status': 'passed', 'eligible_endpoints': ['/articles?page=1']}}))
            rows = [{'target': 'rails-cruby-off', 'endpoint': '/articles?page=1', 'repetition': i,
                     'status': 'passed', 'capacity_rps': 100 + i,
                     'warmup_seconds': 180 + i,
                     'measurement': {'requests_total': 1000, 'requests_failed': 0,
                                     'latency_ms': {'p99': 90 if i == 5 else 20}}}
                    for i in range(1, 6)]
            (root / 'trials/per-run.json').write_text(json.dumps(rows))
            gce_report.generate(root)
            report = (root / 'gce-summary.md').read_text(encoding='utf-8')
            self.assertIn('90.00', report)  # Worst repetition, not median p99.
            self.assertIn('103.00', report)  # Median sustainable RPS.
            rows[-1]['status'] = 'failed'
            (root / 'trials/per-run.json').write_text(json.dumps(rows))
            gce_report.generate(root)
            self.assertIn('4/5 | —', (root / 'gce-summary.md').read_text(encoding='utf-8'))

    def test_tester_network_errors_are_rejected(self):
        raw = 'eth0: 10 2 1 1 0 0 0 0 10 2 3 4 0 0 0 0'
        self.assertEqual(network_errors(raw), 9)
        self.assertEqual(capacity.decision(self.result(100, tester_network_errors=1), self.profile), 'invalid_client')

    def test_remote_command_uses_private_network_and_isolation(self):
        ssh = remote_command('bench-loadgen-c3', 'asia-northeast1-b', 'demo', 'k6 version')
        self.assertIn('--internal-ip', ssh)
        self.assertNotIn('docker', ssh)
        self.assertTrue(remote_copy('bench-loadgen-c3', 'zone', 'demo', 'script.js', '/tmp/test')[-1].startswith('bench-loadgen-c3:'))
        self.assertEqual(run.docker_port_mapping({'remote_loadgen': 'tester', 'target_port': 3000}), '0.0.0.0:3000:3000')
        self.assertEqual(run.docker_port_mapping({}), '127.0.0.1::3000')
        ci = run.config(ROOT / 'bench/profiles/ci-pagination.yml')
        self.assertEqual(ci['fixture_articles'], 1000)
        self.assertEqual(len(ci['targets']), 9)
        p = run.config(ROOT / 'bench/profiles/gce-c3-capacity.yml')
        self.assertEqual(len(p['targets']), 9)
        self.assertEqual(p['fixture_articles'], 1000)
        self.assertEqual(len(run.schedule(p)), 45)

    def test_coarse_overload_recovers_to_lower_confirmed_capacity(self):
        # 25, 50, 100 pass; 200 saturates SUT; recover to 100 and confirm sustained capacity.
        profile = dict(self.profile, capacity_start_rps=25, capacity_max_rps=800,
                       capacity_tolerance_rps=25, capacity_min_rps=25)
        def measure(rate, duration, phase):
            # Server saturates at > 100 RPS
            p99 = 200 if rate > 100 else 20
            return self.result(rate, p99=p99)
        result = capacity.search(measure, profile)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['capacity_rps'], 100)
        phases = [s['phase'] for s in result['steps']]
        self.assertIn('coarse', phases)
        self.assertIn('bracket', phases)
        self.assertEqual(phases[-1], 'confirm')

    def test_tester_cpu_shortage_rejects_without_estimating_capacity(self):
        # When tester CPU is saturated, capacity cannot be estimated; must request loadgen fix/retry.
        def measure(rate, duration, phase):
            return self.result(rate, tester_cpu_pct=92.5)
        with self.assertRaisesRegex(RuntimeError, 'Tester CPU exceeded limit'):
            capacity.search(measure, self.profile)

    def test_confirmation_failure_triggers_lower_candidate_re_search(self):
        # 100 RPS passes 30s exploration, but fails 120s confirmation; search lower candidate (50 RPS).
        profile = dict(self.profile, capacity_start_rps=100, capacity_min_rps=25,
                       capacity_tolerance_rps=25)
        def measure(rate, duration, phase):
            # Server saturates at > 100 RPS during coarse search
            if rate > 100:
                return self.result(rate, p99=200)
            # 100 RPS passes 30s probe but fails 120s confirmation; 50 RPS passes both
            if rate == 100 and phase == 'confirm':
                return self.result(rate, p99=180)  # fails SLO on 120s confirm
            return self.result(rate, p99=20)  # passes
        result = capacity.search(measure, profile)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['capacity_rps'], 50)
        confirm_rates = [s['rate'] for s in result['steps'] if s['phase'] == 'confirm']
        self.assertEqual(confirm_rates, [100, 50])

    def test_corrupted_response_detected_and_rejected(self):
        # HTTP 200 with missing/corrupted body fails response integrity check.
        m = self.result(100, integrity_errors=3)
        cls_info = capacity.classify(m, self.profile)
        self.assertEqual(cls_info['category'], 'sut_slo_fail')
        self.assertIn('integrity', cls_info['reason'].lower())
        self.assertEqual(capacity.decision(m, self.profile), 'slo_fail')

    def test_transport_artifact_error_classification(self):
        m = self.result(100, transport_error=True)
        cls_info = capacity.classify(m, self.profile)
        self.assertEqual(cls_info['category'], 'transport_or_artifact_error')
        self.assertEqual(capacity.decision(m, self.profile), 'transport_error')

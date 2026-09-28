import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import capacity
from loadgen import RemoteLoadGenerator, remote_command, remote_copy
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

    def test_confirmation_failure_invalidates_short_pass(self):
        outcome = capacity.search(lambda rate, duration, phase: self.result(rate, p99=150 if phase == 'confirm' or rate >= 200 else 20), self.profile)
        self.assertIsNone(outcome['capacity_rps'])

    def test_paired_repetitions_exclude_incomplete(self):
        rows = [{'target': t, 'repetition': rep, 'status': 'passed', 'capacity_rps': val}
                for rep, (a, b) in enumerate(((200, 100), (150, 100), (400, 200)), 1)
                for t, val in (('emit-cruby-off', a), ('rails-cruby-off', b))]
        rows[-1]['status'] = 'failed'
        pairs = capacity.paired_ratios(rows, 'emit-cruby-off', 'rails-cruby-off')
        self.assertEqual([p['ratio'] for p in pairs['pairs']], [2, 1.5])
        self.assertEqual(pairs['median'], 1.75)

    def test_remote_command_uses_private_network_and_isolation(self):
        ssh = remote_command('bench-loadgen-c3', 'asia-northeast1-b', 'demo', 'k6 version')
        self.assertIn('--internal-ip', ssh)
        self.assertNotIn('docker', ssh)
        self.assertTrue(remote_copy('bench-loadgen-c3', 'zone', 'demo', 'script.js', '/tmp/test')[-1].startswith('bench-loadgen-c3:'))
        self.assertEqual(run.docker_port_mapping({'remote_loadgen': 'tester', 'target_port': 3000}), '0.0.0.0:3000:3000')
        self.assertEqual(run.docker_port_mapping({}), '127.0.0.1::3000')
        p = run.config(ROOT / 'bench/profiles/gce-c3-capacity.yml')
        self.assertEqual(len(p['targets']), 9)
        self.assertEqual(p['fixture_articles'], 1000)
        self.assertEqual(len(run.schedule(p)), 45)

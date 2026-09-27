"""Exercise the boundary between k6 summaries and the Python trial runner."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import run


class K6ContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed to execute k6 handleSummary functions')
    def test_both_scripts_export_a_normalized_summary(self):
        for name in ('read', 'crud'):
            with self.subTest(name=name):
                source = (ROOT / f'bench/k6/{name}.js').read_text()
                source = '\n'.join(line for line in source.splitlines() if not line.startswith('import '))
                source = source.replace('export const ', 'const ').replace('export default function', 'function defaultFunction')
                source = source.replace('export function ', 'function ')
                harness = """
const __ENV = {SUMMARY_PATH: '/output/k6-summary.json', TARGET_URL: 'http://localhost:3000/articles'};
const Counter = function() {};
const Trend = function() {};
const data = {state: {testRunDurationMs: 10000}, metrics: {
  http_reqs: {values: {count: 500, rate: 50}},
  http_req_duration: {values: {med: 2, 'p(95)': 4, 'p(99)': 8}},
  http_req_failed: {values: {passes: 0}},
  iterations: {values: {count: 500}},
  vus: {values: {max: 5}},
  successful_requests: {values: {count: 500}},
  successful_operations: {values: {count: 500}},
  total_operations: {values: {count: 500}},
}};
""" + source + "\nconsole.log(handleSummary(data)[__ENV.SUMMARY_PATH]);"
                res = subprocess.run(['node', '-e', harness], capture_output=True, text=True, check=True)
                summary = json.loads(res.stdout)
                self.assertEqual(summary['elapsed'], 10)
                self.assertEqual(summary['rps'], 50)
                self.assertEqual(summary['p95_ms'], 4)
                self.assertEqual(summary['latency_ms']['p99'], 8)
                self.assertEqual(summary['requests_failed'], 0)
                self.assertIn('p(99)', source)

    def test_k6_missing_summary_fails_without_closed_loop_fallback(self):
        server = Mock(url='http://localhost:3000')
        p = {'driver': 'k6', 'request_timeout': 5, 'offered_rps': 50}
        cpus = {'client': [1, 2]}
        with tempfile.TemporaryDirectory() as d, \
             patch.object(run.subprocess, 'run', return_value=Mock(returncode=0)), \
             patch.object(run, 'command', return_value='') as command:
            with self.assertRaisesRegex(RuntimeError, 'no normalized summary'):
                run.sample(server, '/articles', 5, p, cpus, d)
            args = command.call_args.args[0]
            self.assertEqual(args[:5], ['taskset', '-c', '1,2', 'k6', 'run'])
            self.assertIn('TARGET_URL=http://localhost:3000/articles', args)

    def test_crud_uses_base_url_and_actual_fixture_size(self):
        server = Mock(url='http://localhost:3000')
        p = {'driver': 'k6', 'k6_script': 'bench/k6/crud.js', 'fixture_articles': 100,
             'request_timeout': 5, 'offered_rps': 20}
        cpus = {'client': [1]}
        with tempfile.TemporaryDirectory() as d, \
             patch.object(run.subprocess, 'run', return_value=Mock(returncode=0)), \
             patch.object(run, 'command', return_value='') as command:
            with self.assertRaises(RuntimeError):
                run.sample(server, '/articles', 5, p, cpus, d)
            args = command.call_args.args[0]
            self.assertIn('TARGET_URL=http://localhost:3000', args)
            self.assertIn('NUM_ARTICLES=100', args)
            self.assertNotIn('TARGET_URL=http://localhost:3000/articles', args)

    def test_docker_k6_writes_summary_as_runner_user(self):
        server = Mock(url='http://localhost:3000')
        p = {'driver': 'k6', 'k6_script': 'bench/k6/crud.js',
             'request_timeout': 5, 'offered_rps': 20}
        with tempfile.TemporaryDirectory() as d, \
             patch.object(run.subprocess, 'run', return_value=Mock(returncode=1)), \
             patch.object(run, 'command', return_value='') as command:
            with self.assertRaisesRegex(RuntimeError, 'no normalized summary'):
                run.sample(server, '/articles', 5, p, {'client': [1]}, d)
            args = command.call_args.args[0]
            self.assertEqual(args[:5], ['docker', 'run', '--rm', '--user',
                                        f'{os.getuid()}:{os.getgid()}'])

    def test_runner_rejects_stale_summary(self):
        server = Mock(url='http://localhost:3000')
        p = {'driver': 'k6', 'request_timeout': 5, 'offered_rps': 50}
        cpus = {'client': [1]}
        with tempfile.TemporaryDirectory() as d, \
             patch.object(run.subprocess, 'run', return_value=Mock(returncode=0)), \
             patch.object(run, 'command', return_value=''):
            path = Path(d) / 'k6-summary.json'
            path.write_text(json.dumps({'driver': 'k6-open-arrival', 'elapsed': 5, 'rps': 50,
                                        'p95_ms': 2, 'requests_total': 250,
                                        'requests_successful': 250, 'requests_failed': 0,
                                        'iterations_dropped': 0, 'client_saturated': False}))
            # A previous summary cannot be returned if the new k6 invocation produces none.
            with self.assertRaisesRegex(RuntimeError, 'no normalized summary'):
                run.sample(server, '/articles', 5, p, cpus, d)


if __name__ == '__main__':
    unittest.main()

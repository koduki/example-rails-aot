#!/usr/bin/env python3
"""Unit tests for smoke profile and preflight reuse."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import run

class SmokeProfileTests(unittest.TestCase):
    def test_smoke_profile_validity_and_schedule(self):
        profile_path = ROOT / 'bench/profiles/smoke.yml'
        self.assertTrue(profile_path.exists(), "smoke.yml profile must exist")
        p = run.config(profile_path)
        self.assertEqual(p['repetitions'], 1)
        self.assertEqual(p['endpoints'], ['/articles'])
        self.assertEqual(p['stable_windows'], 3)
        self.assertLessEqual(p['warmup_min_seconds'], p['warmup_max_seconds'])

        sched = run.schedule(p)
        self.assertEqual(len(sched), len(p['targets']))
        scheduled_targets = {entry['target'] for entry in sched}
        self.assertEqual(scheduled_targets, set(p['targets']))

    def test_preflight_file_reuse_in_main(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            preflight_data = {
                'rails-cruby-off': {'eligible_endpoints': ['/articles'], 'cases': {'/articles': {'status': 'passed'}}},
                'rails-cruby-yjit': {'eligible_endpoints': ['/articles'], 'cases': {'/articles': {'status': 'passed'}}},
            }
            preflight_file = root / 'preflight.json'
            preflight_file.write_text(json.dumps(preflight_data), encoding='utf-8')

            # Run with --preflight-file
            test_args = [
                'run.py', 'run',
                '--profile', str(ROOT / 'bench/profiles/smoke.yml'),
                '--targets', 'rails-cruby-off,rails-cruby-yjit',
                '--preflight-file', str(preflight_file),
                '--output', str(root / 'output'),
                '--app-cpus', '0',
                '--load-cpus', '1',
            ]

            mock_trials = [
                {'target': 'rails-cruby-off', 'endpoint': '/articles', 'repetition': 1, 'status': 'passed', 'measurement': {'errors': 0}},
                {'target': 'rails-cruby-yjit', 'endpoint': '/articles', 'repetition': 1, 'status': 'passed', 'measurement': {'errors': 0}},
            ]

            with patch.object(sys, 'argv', test_args), \
                 patch('run.trials', return_value=mock_trials) as mock_run_trials, \
                 patch('run.preflight') as mock_preflight:
                ret = run.main()
                self.assertEqual(ret, 0)
                mock_preflight.assert_not_called()
                mock_run_trials.assert_called_once()
                self.assertTrue((root / 'output/preflight/preflight.json').exists())
                saved_checks = json.loads((root / 'output/preflight/preflight.json').read_text(encoding='utf-8'))
                self.assertEqual(saved_checks, preflight_data)

if __name__ == '__main__':
    unittest.main()

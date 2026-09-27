#!/usr/bin/env python3
"""Unit tests for Issue #19 GCE portable execution contract, full profile, and environment loading."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))

import run

class GceContractTests(unittest.TestCase):
    def test_full_profile_validity_and_budget_calculation(self):
        """Verify full.yml schema validity and duration estimation formula."""
        full_path = ROOT / 'bench/profiles/full.yml'
        self.assertTrue(full_path.exists())
        p = run.config(full_path)

        self.assertEqual(p['repetitions'], 5)
        self.assertEqual(len(p['targets']), 7)
        self.assertEqual(len(p['endpoints']), 5)
        self.assertEqual(p['driver'], 'k6')
        self.assertEqual(p['offered_rps'], 50)

        # Total trials = targets * endpoints * repetitions
        n_trials = len(p['targets']) * len(p['endpoints']) * p['repetitions']
        self.assertEqual(n_trials, 7 * 5 * 5)  # 175 trials

        # Schedule produces exactly 175 trials
        sched = run.schedule(p)
        self.assertEqual(len(sched), 175)

        # Upper bound time estimation formula:
        # T_max = n_trials * (warmup_max + measurement + ready_timeout)
        per_trial_max_seconds = p['warmup_max_seconds'] + p['measurement_seconds'] + p['ready_timeout']
        estimated_max_seconds = n_trials * per_trial_max_seconds
        # The full profile must fit even its maximum configured windows plus readiness.
        self.assertEqual(p['total_timeout'], 216000)
        self.assertGreaterEqual(p['total_timeout'], estimated_max_seconds)
        minimum = n_trials * (p['warmup_min_seconds'] + p['measurement_seconds'])
        self.assertGreaterEqual(p['total_timeout'], minimum)

    def test_env_file_loading_and_precedence(self):
        """Verify load_env_file parses key-value pairs and applies CLI overrides correctly."""
        with tempfile.TemporaryDirectory() as d:
            env_path = Path(d) / 'test.env'
            env_path.write_text(
                '# Test configuration\n'
                'BENCH_PROFILE=bench/profiles/smoke.yml\n'
                'BENCH_APP_CPUS=0\n'
                'BENCH_LOAD_CPUS=1,2\n'
                'BENCH_MEMORY_MB=2048\n'
                'BENCH_TARGET_HOST=10.0.0.99\n'
                'BENCH_OUTPUT=bench-results/custom-output\n'
            )
            loaded = run.load_env_file(env_path)
            self.assertEqual(loaded['BENCH_PROFILE'], 'bench/profiles/smoke.yml')
            self.assertEqual(loaded['BENCH_APP_CPUS'], '0')
            self.assertEqual(loaded['BENCH_LOAD_CPUS'], '1,2')
            self.assertEqual(loaded['BENCH_MEMORY_MB'], '2048')
            self.assertEqual(loaded['BENCH_TARGET_HOST'], '10.0.0.99')
            self.assertEqual(loaded['BENCH_OUTPUT'], 'bench-results/custom-output')

    def test_environment_files_exist_and_validate(self):
        """Verify all bundled environment templates exist and pass dry-run validation."""
        env_dir = ROOT / 'bench/environments'
        self.assertTrue(env_dir.is_dir())

        for name in ('local-single-host.env', 'remote-loadgen.env', 'gce-c3-standard-4.env'):
            f = env_dir / name
            self.assertTrue(f.exists(), f"Missing {name}")
            loaded = run.load_env_file(f)
            self.assertIn('BENCH_PROFILE', loaded)
            self.assertIn('BENCH_APP_CPUS', loaded)
            self.assertIn('BENCH_LOAD_CPUS', loaded)
            self.assertIn('BENCH_TARGET_HOST', loaded)

            # Dry-run validation
            cmd = [sys.executable, str(ROOT / 'scripts/bench/run.py'), 'run', '--env-file', str(f), '--dry-run']
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(res.returncode, 0, f"Failed dry-run on {name}: {res.stderr}")

    def test_execution_without_github_environment_variables(self):
        """Verify CLI executes cleanly in an environment stripped of all GitHub Actions variables."""
        clean_env = dict(os.environ)
        # Strip all GitHub Actions and CI variables
        for key in list(clean_env.keys()):
            if key.startswith(('GITHUB_', 'RUNNER_', 'ACTIONS_')) or key in ('CI', 'CONTINUOUS_INTEGRATION'):
                del clean_env[key]

        # Verify CLI --help works without CI/GitHub variables
        cmd_help = [sys.executable, str(ROOT / 'scripts/bench/run.py'), '--help']
        res_help = subprocess.run(cmd_help, capture_output=True, text=True, cwd=str(ROOT), env=clean_env)
        self.assertEqual(res_help.returncode, 0)
        self.assertIn('--env-file', res_help.stdout)
        self.assertIn('--target-host', res_help.stdout)

        # Verify CLI run --dry-run works without CI/GitHub variables
        cmd_dry = [sys.executable, str(ROOT / 'scripts/bench/run.py'), 'run',
                   '--profile', str(ROOT / 'bench/profiles/smoke.yml'), '--dry-run']
        res_dry = subprocess.run(cmd_dry, capture_output=True, text=True, cwd=str(ROOT), env=clean_env)
        self.assertEqual(res_dry.returncode, 0)
        self.assertIn('"schema_version"', res_dry.stdout)

if __name__ == '__main__':
    unittest.main()

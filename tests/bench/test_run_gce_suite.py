#!/usr/bin/env python3
"""Unit tests for run_gce_suite.py orchestrator and disk lifecycle management."""
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))

import run_gce_suite


class RunGceSuiteUnitTests(unittest.TestCase):
    def test_verify_artifact_checksums_generates_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'plan.json').write_text('{"profile": "c3"}', encoding='utf-8')
            (root / 'env.json').write_text('{"commit": "abcdef"}', encoding='utf-8')
            trials_dir = root / 'trials'
            trials_dir.mkdir()
            (trials_dir / 'per-run.json').write_text('[{"target": "rails-cruby-off"}]', encoding='utf-8')

            result = run_gce_suite.verify_artifact_checksums(root)
            self.assertEqual(result['total_files'], 3)
            self.assertIn('plan.json', result['checksums'])
            self.assertIn('env.json', result['checksums'])
            self.assertIn('trials/per-run.json', result['checksums'])

            # Verify generated SHA256SUMS file
            sums_file = root / 'SHA256SUMS'
            self.assertTrue(sums_file.exists())
            lines = sums_file.read_text(encoding='utf-8').splitlines()
            self.assertEqual(len(lines), 3)

            expected_plan_hash = hashlib.sha256(b'{"profile": "c3"}').hexdigest()
            self.assertEqual(result['checksums']['plan.json'], expected_plan_hash)

    def test_verify_artifact_checksums_empty_dir_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                run_gce_suite.verify_artifact_checksums(temp)

    def test_disk_lifecycle_guidance_prints_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            tf_dir = Path(temp)
            (tf_dir / 'terraform.tfvars').write_text('project = "test"', encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                res = run_gce_suite.disk_lifecycle_guidance(tf_dir=tf_dir, auto_destroy=False)
            self.assertFalse(res)
            self.assertIn('PERSISTENT DISK COST CONTROL NOTICE', out.getvalue())
            self.assertIn('terraform destroy -var-file=terraform.tfvars', out.getvalue())

    def test_verify_and_start_vms_starts_terminated(self):
        statuses = {'bench-app-c3': ['TERMINATED', 'RUNNING'], 'bench-loadgen-c3': ['RUNNING']}
        calls = []

        def mock_gcloud(*args, **kwargs):
            calls.append(args)
            if 'describe' in args:
                name = args[3]
                st = statuses[name]
                return st.pop(0) if len(st) > 1 else st[0]
            return ""

        with patch.object(run_gce_suite, 'gcloud_cmd', side_effect=mock_gcloud), \
             patch('time.sleep', return_value=None):
            outcomes = run_gce_suite.verify_and_start_vms('p', 'z', ('bench-app-c3', 'bench-loadgen-c3'))
            self.assertEqual(outcomes['bench-app-c3']['status'], 'RUNNING')
            self.assertEqual(outcomes['bench-loadgen-c3']['status'], 'RUNNING')
            # Verify start was issued for app VM
            self.assertTrue(any('start' in c and 'bench-app-c3' in c for c in calls))

    def test_run_suite_failsafe_stops_vms_on_error(self):
        with tempfile.TemporaryDirectory() as temp:
            args = MagicMock()
            args.project = 'p'
            args.zone = 'z'
            args.app_instance = 'bench-app-c3'
            args.loadgen_instance = 'bench-loadgen-c3'
            args.run_id = 'test-run'
            args.output_dir = temp
            args.profile = 'bench/profiles/gce-c3-capacity.yml'
            args.wait_seconds = 10
            args.run_timeout = 60
            args.skip_build = True
            args.skip_preflight = True
            args.auto_destroy = False
            args.terraform_dir = temp
            args.ssh_user = 'test'

            stop_called = []
            def mock_stop(proj, zone, instances, wait_seconds=300):
                stop_called.append((proj, zone, instances))
                return {'bench-app-c3': {'stopped': True}, 'bench-loadgen-c3': {'stopped': True}}

            with patch.object(run_gce_suite, 'verify_and_start_vms', return_value={}), \
                 patch.object(run_gce_suite, 'get_internal_ip', return_value='10.0.0.1'), \
                 patch.object(run_gce_suite, 'ssh_command', side_effect=RuntimeError('Benchmark crashed')), \
                 patch.object(run_gce_suite.gce_cleanup, 'stop_instances', side_effect=mock_stop), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                ret = run_gce_suite.run_suite(args)
                self.assertEqual(ret, 1)
                self.assertEqual(len(stop_called), 1)
                self.assertEqual(stop_called[0][2], ('bench-app-c3', 'bench-loadgen-c3'))


if __name__ == '__main__':
    unittest.main()

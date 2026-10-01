"""Cost-control checks for the external GCE lifecycle helper."""
import importlib.util
import io
import json
import subprocess
import tempfile
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import run_gce_suite
spec = importlib.util.spec_from_file_location('gce_cleanup', ROOT / 'scripts/bench/gce_cleanup.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class GceCleanupTests(unittest.TestCase):
    def test_both_stops_attempted_even_when_first_fails(self):
        calls = []
        def fake_gcloud(action, name, *args):
            calls.append((action, name))
            if name == 'app' and action == 'stop':
                raise subprocess.CalledProcessError(1, 'gcloud')
            if action == 'describe':
                return 'RUNNING' if calls.count(('describe', name)) == 1 else 'TERMINATED'
            return ''
        with patch.object(cleanup, 'gcloud', side_effect=fake_gcloud):
            result = cleanup.stop_instances('project', 'zone', ('app', 'tester'))
        self.assertFalse(result['app']['stopped'])
        self.assertTrue(result['tester']['stopped'])
        self.assertIn(('stop', 'tester'), calls)

    def test_failed_workflow_still_stops_and_records_status(self):
        result = {'app': {'status': 'TERMINATED', 'stopped': True},
                  'tester': {'status': 'TERMINATED', 'stopped': True}}
        with tempfile.TemporaryDirectory() as temp:
            status_file = Path(temp) / 'cleanup.json'
            with patch.object(cleanup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)), \
                 patch.object(cleanup, 'stop_instances', return_value=result) as stop, \
                 redirect_stdout(io.StringIO()):
                exit_code = cleanup.main(['--project', 'p', '--zone', 'z',
                                          '--status-file', str(status_file),
                                          '--after', 'workflow', '--action', 'all'])
            self.assertEqual(exit_code, 7)
            stop.assert_called_once_with('p', 'z', cleanup.INSTANCES, 300)
            self.assertEqual(json.loads(status_file.read_text())['workflow_exit_code'], 7)

    def test_failed_stop_fails_command_even_after_successful_workflow(self):
        with patch.object(cleanup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
             patch.object(cleanup, 'stop_instances', return_value={
                 'app': {'stopped': True}, 'tester': {'stopped': False, 'error': 'denied'}}), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cleanup.main(['--project', 'p', '--zone', 'z',
                                           '--after', 'workflow']), 1)

    def test_interrupted_workflow_still_stops_both(self):
        with patch.object(cleanup.subprocess, 'run', side_effect=KeyboardInterrupt), \
             patch.object(cleanup, 'stop_instances', return_value={
                 'app': {'stopped': True}, 'tester': {'stopped': True}}) as stop, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cleanup.main(['--project', 'p', '--zone', 'z',
                                           '--after', 'workflow']), 130)
        stop.assert_called_once()

    def test_cleanup_retry_verifies_before_mutating_and_preserves_source_manifest(self):
        stopped={name:{'stopped':True,'status':'TERMINATED'} for name in cleanup.INSTANCES}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'plan.json').write_text('{}')
            (root/'env.json').write_text('{}')
            (root/'trials').mkdir();(root/'trials/per-run.json').write_text('[]')
            run_gce_suite.verify_artifact_checksums(root)
            received=(root/'SHA256SUMS').read_bytes()
            args=['--project','p','--zone','z','--status-file',str(root/'cleanup.json'),
                  '--verify-checksums',str(root)]
            with patch.object(cleanup,'stop_instances',return_value=stopped),redirect_stdout(io.StringIO()):
                self.assertEqual(cleanup.main(args),0)
                self.assertEqual(cleanup.main(args),0)
            self.assertEqual((root/'SHA256SUMS.received').read_bytes(),received)
            self.assertTrue(json.loads((root/'cleanup.json').read_text())['artifact_verification_passed'])
            run_gce_suite.verify_artifact_checksums(root)

    def test_corruption_still_stops_both_and_does_not_replace_failed_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'plan.json').write_text('{}')
            with redirect_stdout(io.StringIO()):run_gce_suite.verify_artifact_checksums(root)
            old=(root/'SHA256SUMS').read_bytes();(root/'plan.json').write_text('{"changed":true}')
            stopped={name:{'stopped':True,'status':'TERMINATED'} for name in cleanup.INSTANCES}
            with patch.object(cleanup,'stop_instances',return_value=stopped) as stop, \
                    patch.object(cleanup.subprocess,'run') as destroy,redirect_stdout(io.StringIO()):
                code=cleanup.main(['--project','p','--zone','z','--status-file',str(root/'cleanup.json'),
                                   '--verify-checksums',str(root),'--destroy'])
            self.assertEqual(code,1);stop.assert_called_once()
            destroy.assert_not_called()
            self.assertEqual((root/'SHA256SUMS').read_bytes(),old)
            self.assertFalse(json.loads((root/'cleanup.json').read_text())['artifact_verification_passed'])


if __name__ == '__main__':
    unittest.main()

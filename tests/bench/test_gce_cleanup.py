"""Cost-control checks for the external GCE lifecycle helper."""
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
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


if __name__ == '__main__':
    unittest.main()

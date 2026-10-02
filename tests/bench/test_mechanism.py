"""Check intervention isolation, actual launch flags, and retained FD evidence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/bench'))
import mechanism_plan
import observe
import run


class MechanismTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix' and shutil.which('find'), 'Requires POSIX symlinks and GNU find')
    def test_snapshot_shell_against_descriptor_fixture(self):
        original_run = observe.subprocess.run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'fd').mkdir()
            (root / 'limits').write_text('Max open files            1024                 4096                 files\n')
            for fd in (7, 1023): (root / 'fd' / str(fd)).symlink_to('socket:[42]')
            def local(args, **kwargs):
                script = args[-1].replace('/proc/1/', str(root) + '/')
                return original_run(['sh', '-c', script], **kwargs)
            with patch.object(observe.subprocess, 'run', side_effect=local):
                observed = observe.process_snapshot('fixture-only')
            self.assertEqual(observed['fd_count'], 2)
            self.assertEqual(observed['max_fd'], 1023)
            self.assertEqual(observed['duplicate_socket_fds'], 1)

    def test_profiles_isolate_count_pagination_and_limits(self):
        counts = {}
        for name in mechanism_plan.EXPERIMENTS:
            cells = mechanism_plan.plan(name)
            counts[name] = sum(len(c['profile']['targets']) * c['profile']['repetitions'] for c in cells)
            for cell in cells:
                p = cell['profile']
                self.assertFalse(p['capacity_search']); self.assertFalse(p['diagnostics'])
                self.assertEqual(p['measurement_seconds'], 120)
                self.assertEqual(p['warmup_connections'], 4)
                self.assertTrue(p['measurement_slo_required'])
                self.assertNotIn('rails-jruby-off', p['targets'])
                self.assertEqual(p['offered_rps'], 25 if name == 'spinel-fds' else 10)
        self.assertEqual(counts, {'scaling-cruby':36, 'scaling-jruby':18, 'scaling-spinel':9,
            'pagination-cruby':24, 'pagination-jruby':12, 'pagination-spinel':6, 'spinel-fds':12})
        for pool in (10, 512):
            cells = {c['id']: c['profile'] for c in mechanism_plan.plan('spinel-fds')}
            default = cells[f'pool{pool}-nofiledefault']
            raised = cells[f'pool{pool}-nofile8192']
            self.assertNotIn('container_nofile', default)
            self.assertEqual(raised, dict(default, container_nofile=8192))

    def test_dry_plan_never_executes_and_each_cell_gets_fresh_preflight(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mechanism_plan.retest_plan, 'execute') as execute:
            output = Path(tmp) / 'plan'
            self.assertEqual(mechanism_plan.main(['--experiment','scaling-cruby','--output',str(output)]), 0)
            execute.assert_not_called()
            plan = json.loads((output / 'mechanism-plan.json').read_text())
            self.assertIsNone(plan['executed_cell'])
            self.assertTrue(plan['fresh_preflight_per_cell'])
            self.assertTrue(all('--preflight-file' not in c['argv'] for c in plan['commands']))

    def test_cell_execution_gate_and_budget_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'bad'
            with self.assertRaises(SystemExit):
                mechanism_plan.main(['--experiment','spinel-fds','--output',str(bad),
                                     '--execute-cell','pool10-nofiledefault'])
            self.assertFalse(bad.exists())
            out = Path(tmp) / 'execute'
            with patch.object(mechanism_plan.retest_plan, 'execute', side_effect=subprocess.TimeoutExpired('run', 3600)) as execute:
                status = mechanism_plan.main(['--experiment','spinel-fds','--output',str(out),
                    '--execute-cell','pool512-nofile8192','--remote-loadgen','tester',
                    '--target-host','10.0.0.1','--gce-project','project','--gce-zone','zone'])
            self.assertEqual(status, 1); execute.assert_called_once()
            result = json.loads((out / 'mechanism-result.json').read_text())
            self.assertEqual(result['status'], 'budget_exhausted')
            self.assertEqual(result['exit_code'], 124)

    def test_pid_limits_and_duplicate_socket_fds_are_preserved(self):
        observed = observe.parse_process('Max open files            1024                 4096                 files\n'
            '\n__FD_TARGETS__\n0\t/dev/null\n7\tsocket:[42]\n1023\tsocket:[42]\n')
        self.assertEqual(observed['nofile'], {'soft':'1024','hard':'4096'})
        self.assertEqual(observed['fd_count'], 3)
        self.assertEqual(observed['max_fd'], 1023)
        self.assertEqual(observed['duplicate_socket_fds'], 1)
        self.assertIsNone(observe.parse_process('\n__FD_TARGETS__\n')['nofile'])
        with self.assertRaises(ValueError): observe.parse_process('permission denied')

    def test_container_launch_sets_and_verifies_actual_limit(self):
        p = mechanism_plan.plan('spinel-fds')[2]['profile']
        for actual in ('8192', '1024'):
            with self.subTest(actual=actual), tempfile.TemporaryDirectory() as tmp:
                server = run.Server('spinel', Path(tmp) / 'trial', p, {'app':[0,1,2,3]})
                def command(args, **kwargs):
                    if args[:2] == ['docker','port']: return '0.0.0.0:3000'
                    if '{{.State.Running}}' in args: return 'true'
                    return ''
                client = MagicMock(); client.request.return_value = {'status':200}
                with patch.object(run, 'command', side_effect=command), patch.object(run, 'HttpClient', return_value=client), \
                        patch.object(run, 'check_probe', return_value={}), patch.object(server, 'record_cpu', return_value={}), \
                        patch.object(observe, 'process_snapshot', return_value={'nofile':{'soft':actual,'hard':actual}}):
                    if actual == '8192':
                        server.__enter__(); server.__exit__()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'nofile differs'): server.__enter__()
                argv = json.loads((server.directory / 'launch.json').read_text())['argv']
                self.assertIn('nofile=8192:8192', argv)

    def test_new_fd_fields_reject_bad_values(self):
        base = mechanism_plan.plan('spinel-fds')[0]['profile']
        for fields in ({'container_nofile':True}, {'container_nofile':0}, {'fd_observations':'yes'},
                       {'fd_observations':True, 'socket_observations':False}):
            with self.subTest(fields=fields), self.assertRaises(ValueError): run.config(dict(base, **fields))

    def test_fd_evidence_survives_a_failed_measurement(self):
        collector = MagicMock()
        collector.stop.return_value = {'sample_count':1, 'process_fds':{
            'status':'observed', 'sample_count':1, 'samples':[{'fd_count':1022}], 'errors':[]}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(observe, 'SocketCollector', return_value=collector), \
                patch.object(run, '_sample', side_effect=ValueError('bad summary')):
            with self.assertRaises(ValueError):
                run.sample(MagicMock(), '/articles', 120, {'socket_observations':True,
                    'fd_observations':True}, {}, tmp, phase='measurement')
            observed = json.loads((Path(tmp) / 'fd-observations.json').read_text())
            self.assertEqual(observed['samples'][0]['fd_count'], 1022)
            self.assertEqual(json.loads((Path(tmp) / 'phase.json').read_text())['status'], 'failed')
            self.assertNotIn('process_fds', json.loads((Path(tmp) / 'socket-observations.json').read_text()))

    def test_fd_observation_errors_are_not_zero_fd_samples(self):
        collector = observe.SocketCollector('container', fd_observations=True)
        collector.event = MagicMock(); collector.event.is_set.side_effect = [False, True]
        process = MagicMock(); process.stdout = ''
        with patch.object(observe.subprocess, 'run', return_value=process), \
                patch.object(observe, 'process_snapshot', side_effect=OSError('access denied')):
            collector._run()
        result = collector.stop()['process_fds']
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['samples'], [])
        self.assertEqual(len(result['errors']), 1)

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import preflight
import prepare
import run
import prepare_app

class BenchmarkTests(unittest.TestCase):
    def test_fixture_is_deterministic_and_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d)/'a.db', Path(d)/'b.db'
            self.assertEqual(prepare.prepare(a), prepare.prepare(b))
            with self.assertRaises(ValueError):
                prepare.prepare(a)
            db = sqlite3.connect(a)
            try:
                self.assertEqual(db.execute('SELECT count(*) FROM articles').fetchone()[0], 3)
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            finally:
                db.close()

    def test_json_fallback_and_wrong_body_are_rejected(self):
        bad = {'status':200, 'content_type':'text/html', 'location':None, 'body':'<h1>Not JSON</h1>'}
        with self.assertRaises(ValueError):
            preflight.canonical(bad, True)
        good = dict(bad, content_type='application/json', body='{"id":1}')
        reference = {'cases':{'/articles.json':{'status':'passed', 'canonical':preflight.canonical(good,True)}}}
        candidate = copy.deepcopy(reference)
        candidate['cases']['/articles.json']['canonical']['body']['id'] = 99
        result = preflight.compare(reference,candidate)
        self.assertEqual(result['eligible_endpoints'], [])
        self.assertEqual(result['cases']['/articles.json']['difference']['path'],
                         '$.canonical.body.id')

    def test_csrf_exclusion_retains_observed_http_and_database_effect(self):
        reference = {'cases': {'csrf_invalid': {'status': 'failed',
            'raw': {'status': 302}, 'write_persisted': True}}}
        candidate = {'cases': {'csrf_invalid': {'status': 'failed',
            'raw': {'status': 303}, 'write_persisted': True}}}
        case = preflight.compare(reference, candidate)['cases']['csrf_invalid']
        self.assertEqual(case['status'], 'excluded')
        self.assertEqual(case['observed_http_status'], 303)
        self.assertTrue(case['observed_write_persisted'])

    def test_timestamps_are_not_blindly_removed(self):
        a = {'status':200, 'content_type':'application/json','location':None,
             'body':'{"created_at":"2025-01-01T00:00:00Z"}'}
        b = dict(a,body='{"created_at":"2026-01-01T00:00:00Z"}')
        self.assertNotEqual(preflight.canonical(a),preflight.canonical(b))
        self.assertEqual(preflight.timestamp('2026-09-27 00:33:58 UTC'),
                         preflight.timestamp('2026-09-27T00:33:58Z'))

    def test_database_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'data.db'; prepare.prepare(path)
            initial=preflight.snapshot(path)
            actual=copy.deepcopy(initial); actual['articles'][0]['updated_at']='1990-01-01 00:00:00'
            with self.assertRaises(ValueError):
                preflight.canonical_db(actual, initial, 1700000000, 1700000001)

    def test_html_tokens_only_normalized(self):
        base={'status':200,'content_type':'text/html','location':None}
        a=dict(base,body='<main><input name="authenticity_token" value="aaa"><p>Title</p></main>')
        b=dict(base,body='<main><input value="bbb" name="authenticity_token"><p>Title</p></main>')
        self.assertEqual(preflight.canonical(a),preflight.canonical(b))
        b['body']=b['body'].replace('Title','Wrong')
        self.assertNotEqual(preflight.canonical(a),preflight.canonical(b))

    def test_disabled_csrf_markup_is_excluded_but_form_fields_are_compared(self):
        base = {'status': 200, 'content_type': 'text/html', 'location': None}
        rails = dict(base, body='<main><form><input name="article[title]" value="A"></form></main>')
        emitted = dict(base, body='<main><form><input name="authenticity_token" value="token"><input name="article[title]" value="A"></form></main>')
        self.assertEqual(preflight.canonical(rails), preflight.canonical(emitted))
        emitted['body'] = emitted['body'].replace('value="A"', 'value="B"')
        self.assertNotEqual(preflight.canonical(rails), preflight.canonical(emitted))

    def test_aot_html_comparison_keeps_method_and_invalid_field_content(self):
        rails = '<main><div class="field_with_errors"><label>Title</label></div><input autocomplete="off" name="_method" value="delete"></main>'
        emitted = '<main><label>Title</label><input name="_method" value="delete"></main>'
        self.assertEqual(preflight.legacy.normalize_html(rails),
                         preflight.legacy.normalize_html(emitted))
        self.assertNotEqual(preflight.legacy.normalize_html(rails),
                            preflight.legacy.normalize_html(emitted.replace('delete', 'patch')))

    def test_probe_rejects_jit_and_db_mismatch(self):
        info={'runtime':'ruby','jit':'off','yjit_enabled':True,'pragmas':preflight.PRAGMAS}
        response={'status':200,'body':json.dumps(info)}
        with self.assertRaises(ValueError):
            preflight.check_probe(response, {'runtime':'cruby','jit':'off'})
        info['yjit_enabled']=False
        self.assertEqual(preflight.check_probe(dict(response,body=json.dumps(info)), {'runtime':'cruby','jit':'off'}),info)

    def test_schedule_reproducible_balanced(self):
        p=run.config(ROOT/'bench/profiles/quick.yml')
        a=run.schedule(p)
        self.assertEqual(a,run.schedule(p))
        for t in p['targets']:
            self.assertEqual(sum(x['target']==t for x in a),len(p['endpoints'])*p['repetitions'])
        self.assertNotEqual(a[:7],a[14:21])

    def test_stability_rejects_errors_and_trend(self):
        p=run.config(ROOT/'bench/profiles/quick.yml')
        rows=[{'rps':100,'p95_ms':5,'errors':0} for _ in range(4)]
        self.assertTrue(run.stable(rows,p))
        rows[-1]['rps']=200
        self.assertFalse(run.stable(rows,p))
        rows[-1]['rps']=100; rows[-1]['errors']=1
        self.assertFalse(run.stable(rows,p))

    def test_jruby_warmup_tolerates_sparse_errors_but_rejects_high_rate(self):
        p=run.config(ROOT/'bench/profiles/ci-jruby-convergence.yml')
        rows=[{'rps':100, 'p95_ms':5, 'errors':2, 'requests_total':1000} for _ in range(4)]
        self.assertTrue(run.stable(rows,p))
        rows[-1]['errors']=6
        self.assertFalse(run.stable(rows,p))

    def test_source_copy_does_not_mutate_original(self):
        before=prepare_app.tree_hash(ROOT/'blog')
        with tempfile.TemporaryDirectory() as d:
            prepared=prepare_app.prepare(Path(d)/'app')
            self.assertEqual(prepared['source_hash'], before)
            self.assertEqual((Path(d)/'app/config/application.rb').read_bytes(),
                             (ROOT/'blog/config/application.rb').read_bytes())
            self.assertIn('config.load_defaults 8.0',(Path(d)/'app/config/application.rb').read_text())
        self.assertEqual(before,prepare_app.tree_hash(ROOT/'blog'))

if __name__ == '__main__':
    unittest.main()

class LifecycleTests(unittest.TestCase):
    def test_cleanup_even_when_log_collection_fails(self):
        from unittest.mock import patch
        import subprocess
        p = run.config(ROOT/'bench/profiles/quick.yml')
        with tempfile.TemporaryDirectory() as directory:
            server = run.Server('rails-cruby-off',directory,p,{'app':[0],'client':[1]})
            calls = []
            def fake_command(args, **kwargs):
                calls.append(args)
                if args[1] == 'logs':
                    raise subprocess.TimeoutExpired(args, 30)
                return '[]'
            with patch.object(run, 'command', side_effect=fake_command):
                server.__exit__(None,None,None)
            self.assertEqual(calls[-1][:3], ['docker','rm','-f'])

    def test_cpu_overlap_is_rejected(self):
        from unittest.mock import patch
        p=run.config(ROOT/'bench/profiles/quick.yml')
        with patch.object(run.os,'sched_getaffinity',create=True,return_value={0,1}):
            with self.assertRaises(ValueError): run.allocation(p,'0','0')
            self.assertEqual(run.allocation(p)['client'],[1])
        self.assertEqual(run.cpuset('1,3-5'),{1,3,4,5})

    def test_report_keeps_ineligible_results_out_of_common_reads(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            run.save(root/'preflight/preflight.json',{
                'rails-cruby-off':{'eligible_endpoints':['/articles','/articles.json'],
                                   'cases':{'/articles':{'status':'passed'}}},
                'emit-cruby-off':{'eligible_endpoints':['/articles'],
                                  'cases':{'/articles.json':{'status':'failed'}}}})
            run.save(root/'trials/per-run.json',[
                {'target':'emit-cruby-off','endpoint':'/articles.json','repetition':1,
                 'status':'excluded','reason':'Endpoint failed preflight'}])
            result=run.report(root)
            self.assertEqual(result['eligible_common_reads'],['/articles'])
            self.assertEqual(result['trial_status_counts'],{'excluded':1})
            self.assertNotIn('rps',json.dumps(result))

    def test_warmup_unrecoverable_early_exit_on_client_saturation(self):
        p = {'warmup_fail_fast_windows': 3}
        windows = [
            {'client_saturated': True, 'iterations_dropped': 5, 'p95_ms': 50, 'requests_total': 100, 'requests_failed': 0},
            {'client_saturated': True, 'iterations_dropped': 8, 'p95_ms': 60, 'requests_total': 100, 'requests_failed': 0},
            {'client_saturated': True, 'iterations_dropped': 12, 'p95_ms': 70, 'requests_total': 100, 'requests_failed': 0},
        ]
        aborted, reason = run.warmup_unrecoverable(windows, p)
        self.assertTrue(aborted)
        self.assertIn('client saturated', reason)

    def test_warmup_unrecoverable_early_exit_on_high_error_rate(self):
        p = {'warmup_fail_fast_windows': 3, 'warmup_fail_fast_error_rate': 0.2}
        windows = [
            {'requests_total': 100, 'requests_failed': 30, 'p95_ms': 50},
            {'requests_total': 100, 'requests_failed': 40, 'p95_ms': 60},
            {'requests_total': 100, 'requests_failed': 50, 'p95_ms': 70},
        ]
        aborted, reason = run.warmup_unrecoverable(windows, p)
        self.assertTrue(aborted)
        self.assertIn('error rate exceeded', reason)

    def test_warmup_unrecoverable_early_exit_on_high_latency(self):
        p = {'warmup_fail_fast_windows': 3, 'warmup_max_latency_ms': 1000.0}
        windows = [
            {'requests_total': 100, 'requests_failed': 0, 'p95_ms': 1500.0},
            {'requests_total': 100, 'requests_failed': 0, 'p95_ms': 2200.0},
            {'requests_total': 100, 'requests_failed': 0, 'p95_ms': 3000.0},
        ]
        aborted, reason = run.warmup_unrecoverable(windows, p)
        self.assertTrue(aborted)
        self.assertIn('latency exceeded', reason)

    def test_warmup_unrecoverable_does_not_abort_when_healthy(self):
        p = {'warmup_fail_fast_windows': 3, 'warmup_max_latency_ms': 1000.0, 'warmup_fail_fast_error_rate': 0.2}
        windows = [
            {'requests_total': 100, 'requests_failed': 0, 'p95_ms': 20.0},
            {'requests_total': 100, 'requests_failed': 0, 'p95_ms': 25.0},
            {'requests_total': 100, 'requests_failed': 0, 'p95_ms': 22.0},
        ]
        aborted, reason = run.warmup_unrecoverable(windows, p)
        self.assertFalse(aborted)
        self.assertIsNone(reason)

    def test_stable_respects_target_specific_max_cv_and_drift(self):
        windows = [
            {'rps': 100.0, 'p95_ms': 20.0, 'errors': 0, 'requests_total': 100},
            {'rps': 115.0, 'p95_ms': 20.0, 'errors': 0, 'requests_total': 100},
            {'rps': 100.0, 'p95_ms': 20.0, 'errors': 0, 'requests_total': 100},
            {'rps': 115.0, 'p95_ms': 20.0, 'errors': 0, 'requests_total': 100},
        ]
        p_strict = {'stable_windows': 4, 'max_cv': 0.05, 'max_drift': 0.05, 'warmup_max_error_rate': 0}
        self.assertFalse(run.stable(windows, p_strict, target='spinel'))

        p_target = {'stable_windows': 4, 'max_cv': 0.05, 'max_drift': 0.08, 'warmup_max_error_rate': 0,
                    'target_max_cv': {'spinel': 0.08}}
        self.assertTrue(run.stable(windows, p_target, target='spinel'))
        self.assertFalse(run.stable(windows, p_target, target='rails-cruby-off'))

    def test_config_validates_adaptive_warmup_and_capacity_settings(self):
        base = run.config(ROOT / 'bench/profiles/quick.yml')
        good = dict(base, target_capacity_start_rps={'rails-cruby-off': 25},
                    capacity_min_rps=25, target_max_cv={'spinel': 0.08},
                    warmup_fail_fast_windows=3, warmup_max_latency_ms=2000.0,
                    warmup_fail_fast_error_rate=0.2)
        self.assertIsInstance(run.config(good), dict)

        bad_target_rps = dict(base, target_capacity_start_rps={'rails-cruby-off': -5})
        with self.assertRaises(ValueError):
            run.config(bad_target_rps)

        bad_cv = dict(base, target_max_cv={'spinel': 1.5})
        with self.assertRaises(ValueError):
            run.config(bad_cv)

        bad_fail_fast = dict(base, warmup_fail_fast_windows=0)
        with self.assertRaises(ValueError):
            run.config(bad_fail_fast)

    def test_cli_and_env_override_capacity_and_warmup_settings(self):
        from unittest.mock import patch
        import io
        cmd = [
            'run.py', 'run', '--dry-run',
            '--capacity-start-rps', '50',
            '--capacity-min-rps', '20',
            '--target-capacity-start-rps', 'rails-cruby-off=25,spinel=200',
            '--max-cv', '0.08',
            '--max-drift', '0.09',
            '--warmup-fail-fast-windows', '4',
            '--warmup-max-latency-ms', '1500',
        ]
        with patch.object(sys, 'argv', cmd), patch('sys.stdout', new_callable=io.StringIO) as out:
            ret = run.main()
            self.assertEqual(ret, 0)
            plan = json.loads(out.getvalue())
            p = plan['profile']
            self.assertEqual(p['capacity_start_rps'], 50)
            self.assertEqual(p['capacity_min_rps'], 20)
            self.assertEqual(p['target_capacity_start_rps'], {'rails-cruby-off': 25, 'spinel': 200})
            self.assertEqual(p['max_cv'], 0.08)
            self.assertEqual(p['max_drift'], 0.09)
            self.assertEqual(p['warmup_fail_fast_windows'], 4)
            self.assertEqual(p['warmup_max_latency_ms'], 1500.0)

    def test_trials_early_abort_skips_measurement_and_marks_unstable(self):
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as d:
            output = Path(d)
            p = dict(run.config(ROOT / 'bench/profiles/quick.yml'),
                     repetitions=1, targets=['rails-cruby-off'], endpoints=['/articles'],
                     warmup_min_seconds=30, warmup_max_seconds=300, window_seconds=10,
                     warmup_fail_fast_windows=3, warmup_max_latency_ms=1000.0)
            cpus = {'app': [0], 'client': [1], 'allowed': [0, 1]}
            checks = {'rails-cruby-off': {'eligible_endpoints': ['/articles']}}

            # Mock sample to return latency > 1000ms
            unhealthy_window = {
                'elapsed': 10.0, 'rps': 10.0, 'p95_ms': 2500.0, 'requests_total': 100,
                'requests_successful': 100, 'requests_failed': 0, 'iterations_dropped': 0,
                'client_saturated': False, 'latency_ms': {'p95': 2500.0, 'p99': 3000.0}
            }
            mock_server = MagicMock()
            mock_server.url = 'http://127.0.0.1:3000'
            mock_server.database = output / 'fake.db'
            mock_server.__enter__.return_value = mock_server
            mock_server.get_diagnostics.return_value = None

            with patch.object(run, 'Server', return_value=mock_server), \
                 patch.object(run, 'sample', return_value=unhealthy_window) as mock_sample:
                rows = run.trials(p, cpus, output, checks)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['status'], 'unstable')
                self.assertIn('latency exceeded', rows[0]['reason'])
                # Only warmup windows sampled, no measurement step
                self.assertNotIn('measurement', rows[0])
                # Warmup ran exactly 3 windows (30s >= warmup_min_seconds 30s)
                self.assertEqual(rows[0]['warmup_seconds'], 30.0)

    def test_trials_passes_target_specific_start_rps_to_capacity_search(self):
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as d:
            output = Path(d)
            p = dict(run.config(ROOT / 'bench/profiles/quick.yml'),
                     repetitions=1, targets=['rails-cruby-off'], endpoints=['/articles'],
                     warmup_min_seconds=10, warmup_max_seconds=60, window_seconds=10,
                     capacity_search=True, capacity_start_rps=100,
                     target_capacity_start_rps={'rails-cruby-off': 25})
            cpus = {'app': [0], 'client': [1], 'allowed': [0, 1]}
            checks = {'rails-cruby-off': {'eligible_endpoints': ['/articles']}}

            clean_window = {
                'elapsed': 10.0, 'rps': 100.0, 'p95_ms': 20.0, 'requests_total': 1000,
                'requests_successful': 1000, 'requests_failed': 0, 'iterations_dropped': 0,
                'client_saturated': False, 'latency_ms': {'p95': 20.0, 'p99': 25.0}
            }
            mock_server = MagicMock()
            mock_server.url = 'http://127.0.0.1:3000'
            mock_server.name = 'rails-cruby-off'
            mock_server.__enter__.return_value = mock_server
            mock_server.get_diagnostics.return_value = None

            search_profile_received = []
            def fake_search(measure, profile):
                search_profile_received.append(dict(profile))
                return {'offered_rps': 25, 'capacity_rps': 25, 'measurement': clean_window, 'status': 'pass'}

            with patch.object(run, 'Server', return_value=mock_server), \
                 patch.object(run, 'sample', return_value=clean_window), \
                 patch.object(run, 'stable', return_value=True), \
                 patch.object(run.capacity, 'search', side_effect=fake_search):
                rows = run.trials(p, cpus, output, checks)
                self.assertEqual(len(rows), 1)
                self.assertEqual(len(search_profile_received), 1)
                self.assertEqual(search_profile_received[0]['capacity_start_rps'], 25)

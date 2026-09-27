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
            with sqlite3.connect(a) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM articles').fetchone()[0], 3)
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')

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

    def test_source_copy_does_not_mutate_original(self):
        before=prepare_app.tree_hash(ROOT/'blog')
        with tempfile.TemporaryDirectory() as d:
            prepared=prepare_app.prepare(Path(d)/'app')
            self.assertEqual(prepared['source_hash'], before)
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
        with patch.object(run.os,'sched_getaffinity',return_value={0,1}):
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

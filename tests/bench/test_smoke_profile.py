#!/usr/bin/env python3
"""Unit tests for smoke profile and preflight reuse."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import run
from prepare import prepare
from preflight import snapshot

class SmokeProfileTests(unittest.TestCase):
    def test_crud_database_checks_detect_real_writes_and_leaks(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'fixture.sqlite3'
            prepare(path, count=100)
            original = snapshot(path)
            initial_sequence = run.article_sequence(path)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE articles SET title=?, body=? WHERE id=1',
                           ('Article 1 (iteration 0)',
                            'Updated body for article 1 at iteration 0. Preserves bounded storage.'))
            changed = snapshot(path)
            measurement = {'iterations_completed': 1,
                           'operations': {'total': 1, 'successful': 1, 'writes': 1}}
            self.assertEqual(run.verify_crud_state(original, changed, initial_sequence,
                             run.article_sequence(path), measurement, 'mix', 100)['updated_articles'], 1)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE articles SET body=? WHERE id=1', ('unexpected',))
            with self.assertRaisesRegex(ValueError, 'not persisted'):
                run.verify_crud_state(original, snapshot(path), initial_sequence,
                                      run.article_sequence(path), measurement, 'mix', 100)

            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO articles (title,body,created_at,updated_at) VALUES (?,?,?,?)',
                           ('temp', 'body', '2025-01-01', '2025-01-01'))
                created_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
                db.execute('DELETE FROM articles WHERE id=?', (created_id,))
            self.assertEqual(run.verify_crud_state(snapshot(path), snapshot(path),
                             initial_sequence, run.article_sequence(path), measurement,
                             'create_delete', 100)['created_and_deleted'], 1)
            with self.assertRaisesRegex(ValueError, 'sequence'):
                run.verify_crud_state(snapshot(path), snapshot(path), initial_sequence,
                                      initial_sequence, measurement, 'create_delete', 100)

    def test_verification_profiles_are_functional_smokes(self):
        for profile in ('crud.yml', 'crud-create-delete.yml'):
            p = run.config(ROOT / 'bench/profiles' / profile)
            self.assertTrue(p['verification_only'])
            self.assertFalse(p['allow_unstable'])
            self.assertEqual(set(p['targets']), set(run.TARGETS['targets']))

    def test_crud_gate_selects_only_workload_operations_and_fails_closed(self):
        checks = {'rails': {'cases': {
            'create': {'status': 'passed'}, 'delete': {'status': 'passed'},
            'update': {'status': 'passed'}, 'csrf_invalid': {'status': 'failed'},
            'create_invalid': {'status': 'failed'}}},
            'emitted': {'cases': {'create': {'status': 'passed'},
                                  'update': {'status': 'failed'}}}}
        self.assertEqual(run.crud_gate(checks, ['rails'], 'mix'), {})
        self.assertEqual(run.crud_gate(checks, ['rails', 'emitted'], 'mix'),
                         {'emitted': ['update']})
        self.assertEqual(run.crud_gate(checks, ['rails', 'emitted'], 'create_delete'),
                         {'emitted': ['delete']})
        self.assertEqual(run.crud_gate(checks, ['absent'], 'update'),
                         {'absent': ['update']})

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
            with patch('run.preflight_identity', return_value={'image_ids': {'app': 'sha256:fixed'}}):
                run.save_preflight_manifest(preflight_file, list(preflight_data))

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
                 patch('run.preflight_identity', return_value={'image_ids': {'app': 'sha256:fixed'}}), \
                 patch('run.trials', return_value=mock_trials) as mock_run_trials, \
                 patch('run.preflight') as mock_preflight:
                ret = run.main()
                self.assertEqual(ret, 0)
                mock_preflight.assert_not_called()
                mock_run_trials.assert_called_once()
                self.assertTrue((root / 'output/preflight/preflight.json').exists())
                self.assertTrue((root / 'output/preflight/preflight-manifest.json').exists())
                saved_checks = json.loads((root / 'output/preflight/preflight.json').read_text(encoding='utf-8'))
                self.assertEqual(saved_checks, preflight_data)

    def test_preflight_reuse_rejects_stale_source_images_and_result_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'preflight.json'
            data = {'rails-cruby-off': {'eligible_endpoints': ['/articles']},
                    'rails-cruby-yjit': {'eligible_endpoints': ['/articles']}}
            run.save(path, data)
            with patch('run.preflight_identity', return_value={'image_ids': {'rails': 'sha256:original'}}):
                run.save_preflight_manifest(path, list(data))
                self.assertEqual(run.reuse_preflight(path, ['rails-cruby-yjit']), data)
            with patch('run.preflight_identity', return_value={'image_ids': {'rails': 'sha256:changed'}}):
                with self.assertRaisesRegex(ValueError, 'images changed'):
                    run.reuse_preflight(path, ['rails-cruby-yjit'])
            with patch('run.preflight_identity', return_value={'image_ids': {'rails': 'sha256:original'}}):
                with self.assertRaisesRegex(ValueError, 'target coverage'):
                    run.reuse_preflight(path, ['spinel'])
                run.save(path, {'rails-cruby-off': {'eligible_endpoints': []}})
                with self.assertRaisesRegex(ValueError, 'results changed'):
                    run.reuse_preflight(path, ['rails-cruby-off'])

    def test_report_failure_marks_run_failed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            check_file = root / 'preflight.json'
            check_file.write_text(json.dumps({'rails-cruby-off': {
                'eligible_endpoints': ['/articles'], 'cases': {'/articles': {'status': 'passed'}}
            }}))
            with patch('run.preflight_identity', return_value={'image_ids': {'app': 'sha256:fixed'}}):
                run.save_preflight_manifest(check_file, ['rails-cruby-off'])
            args = ['run.py', 'run', '--profile', str(ROOT / 'bench/profiles/smoke.yml'),
                    '--targets', 'rails-cruby-off', '--preflight-file', str(check_file),
                    '--output', str(root / 'output'), '--app-cpus', '0', '--load-cpus', '1']
            rows = [{'target': 'rails-cruby-off', 'endpoint': '/articles', 'repetition': 1,
                     'status': 'passed', 'measurement': {'rps': 10}}]
            with patch.object(sys, 'argv', args), patch('run.trials', return_value=rows), \
                 patch('run.preflight_identity', return_value={'image_ids': {'app': 'sha256:fixed'}}), \
                 patch('run.report', side_effect=RuntimeError('report corrupted')):
                self.assertEqual(run.main(), 1)
            failure = json.loads((root / 'output/failure.json').read_text())
            self.assertIn('report corrupted', failure['reason'])

if __name__ == '__main__':
    unittest.main()

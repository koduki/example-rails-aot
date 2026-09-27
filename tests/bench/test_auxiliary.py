#!/usr/bin/env python3
"""Unit tests for auxiliary benchmark evaluations (Issue #21)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import auxiliary
import prepare
import run

class AuxiliaryUnitTests(unittest.TestCase):
    def test_check_cpu_budget(self):
        # When host has 4 CPUs and needs 4 app + 2 load = 6
        with patch('os.sched_getaffinity', return_value={0, 1, 2, 3}, create=True):
            res = auxiliary.check_cpu_budget(required_app_cpus=4, min_load_cpus=2)
            self.assertFalse(res['feasible'])
            self.assertEqual(res['disposition'], 'skipped_due_to_resource_limit')
            self.assertIn('Hosted runner execution is skipped', res['reason'])

        # When host has 8 CPUs and needs 4 app + 2 load = 6
        with patch('os.sched_getaffinity', return_value=set(range(8)), create=True):
            res = auxiliary.check_cpu_budget(required_app_cpus=4, min_load_cpus=2)
            self.assertTrue(res['feasible'])
            self.assertEqual(res['disposition'], 'executable')
            self.assertIsNone(res['reason'])

    def test_prepare_large_fixture_and_pagination(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / 'test_large.sqlite3'
            res = prepare.prepare(db_path, count=50, comments_per_article=2)
            self.assertEqual(res['articles'], 50)
            self.assertEqual(res['comments'], 100)
            self.assertTrue(db_path.exists())

            # Test page 1 pagination (20 per page)
            page1 = prepare.fetch_page(db_path, page=1, per_page=20)
            self.assertEqual(page1['page'], 1)
            self.assertEqual(page1['per_page'], 20)
            self.assertEqual(page1['total_items'], 50)
            self.assertEqual(page1['total_pages'], 3)
            self.assertEqual(len(page1['items']), 20)
            self.assertEqual(page1['items'][0]['comments_count'], 2)

            # Test page 3 pagination (remaining 10 items)
            page3 = prepare.fetch_page(db_path, page=3, per_page=20)
            self.assertEqual(page3['page'], 3)
            self.assertEqual(len(page3['items']), 10)

    def test_quick_4core_profile_validity(self):
        profile_path = ROOT / 'bench/profiles/quick-4core.yml'
        self.assertTrue(profile_path.exists())
        p = run.config(profile_path)
        self.assertEqual(p['cpu_count'], 4)
        self.assertEqual(p['memory_mb'], 4096)
        self.assertEqual(p['threads'], 4)
        self.assertEqual(p['spinel_workers'], 4)
        self.assertEqual(p['repetitions'], 3)

    def test_crud_profile_validity(self):
        profile_path = ROOT / 'bench/profiles/crud.yml'
        self.assertTrue(profile_path.exists())
        p = run.config(profile_path)
        self.assertEqual(p['cpu_count'], 1)
        self.assertEqual(p['driver'], 'k6')
        self.assertEqual(p['k6_script'], 'bench/k6/crud.js')
        self.assertEqual(p['offered_rps'], 2)
        self.assertEqual(p['crud_scenario'], 'mix')
        create_delete = run.config(ROOT / 'bench/profiles/crud-create-delete.yml')
        self.assertEqual(create_delete['offered_rps'], 1)
        self.assertEqual(create_delete['crud_scenario'], 'create_delete')
        crud_js = ROOT / p['k6_script']
        self.assertTrue(crud_js.exists())

    def test_build_auxiliary_report_e2e(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            trials_dir = root / 'trials'
            trials_dir.mkdir()

            # Create mock trial directory
            t1 = trials_dir / '0001-spinel'
            t1.mkdir()
            (t1 / 'runtime.json').write_text(json.dumps({'runtime': 'spinel', 'jit': 'aot', 'ready_seconds': 0.15}))
            (t1 / 'trial.json').write_text(json.dumps({'target': 'spinel'}))

            t2 = trials_dir / '0002-rails-cruby-off'
            t2.mkdir()
            (t2 / 'runtime.json').write_text(json.dumps({'runtime': 'ruby', 'jit': 'off', 'ready_seconds': 2.35}))
            (t2 / 'trial.json').write_text(json.dumps({'target': 'rails-cruby-off'}))

            out_dir = root / 'out'
            report_data, md = auxiliary.build_auxiliary_report(run_dir=root, output_path=out_dir)

            self.assertTrue((out_dir / 'auxiliary.json').exists())
            self.assertTrue((out_dir / 'auxiliary.md').exists())
            self.assertIn('# Benchmark P2 Auxiliary Evaluations Report', md)
            self.assertIn('## 1. 4-Core Execution Feasibility', md)
            self.assertIn('## 2. Startup Latency to First Business Response', md)
            self.assertIn('`spinel`', md)
            self.assertIn('0.15s', md)
            self.assertIn('`rails-cruby-off`', md)
            self.assertIn('2.35s', md)

if __name__ == '__main__':
    unittest.main()

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('ci_report', ROOT / 'scripts/bench/ci_report.py')
ci_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_report)


class CiReportModeTests(unittest.TestCase):
    def test_quick_report_states_heavy_checks_were_not_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / 'bench-results'
            results.mkdir()
            (results / 'unit-status.json').write_text(json.dumps({'status': 'passed'}))
            (results / 'terraform-status.json').write_text(json.dumps({'status': 'passed'}))
            with patch.object(ci_report, 'ROOT', root), \
                 patch.object(ci_report, 'DEST', results / 'ci-verification-report.md'), \
                 patch.dict(os.environ, {'BENCH_CI_MODE': 'quick', 'GITHUB_SHA': 'test-commit'}):
                text = ci_report.generate().read_text()
        self.assertIn('Mode: `quick`', text)
        self.assertIn('not run in quick mode', text)
        self.assertNotIn('| Target | Build |', text)
        self.assertIn('Terraform fmt/init/validate: `passed`', text)


if __name__ == '__main__':
    unittest.main()

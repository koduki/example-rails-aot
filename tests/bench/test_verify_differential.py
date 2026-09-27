import unittest
import importlib.util
from pathlib import Path

# Dynamically import verify_differential.py from .agents/skills/cross-runtime-verifier/scripts/
script_path = Path(__file__).parent.parent.parent / ".agents" / "skills" / "cross-runtime-verifier" / "scripts" / "verify_differential.py"
spec = importlib.util.spec_from_file_location("verify_differential", str(script_path))
verify_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_diff)


class VerifyDifferentialTests(unittest.TestCase):
    def test_normalize_html(self):
        html = '<meta name="csrf-token" content="secret123"><div class="content">   Hello \n world  </div>'
        normalized = verify_diff.normalize_html(html)
        self.assertNotIn("csrf-token", normalized)
        self.assertEqual(normalized, '<div class="content"> Hello world </div>')

    def test_normalize_json(self):
        json_str = '{"b": 2, "a": 1}'
        normalized = verify_diff.normalize_json(json_str)
        self.assertEqual(normalized, '{"a": 1, "b": 2}')

    def test_format_markdown_report(self):
        report = {
            "all_eligible": True,
            "endpoints": [
                {
                    "path": "/articles",
                    "accept": "text/html",
                    "baseline_status": 200,
                    "candidates": {
                        "spinel": {
                            "status": 200,
                            "verdict": "MATCH"
                        }
                    }
                }
            ]
        }
        md = verify_diff.format_markdown_report(report, "baseline", ["spinel"])
        self.assertIn("✅ ALL ELIGIBLE", md)
        self.assertIn("`/articles`", md)
        self.assertIn("✅ MATCH (200)", md)


if __name__ == "__main__":
    unittest.main()

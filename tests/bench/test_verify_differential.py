import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


script_path = Path(__file__).resolve().parents[2] / ".agents/skills/cross-runtime-verifier/scripts/verify_differential.py"
spec = importlib.util.spec_from_file_location("verify_differential", script_path)
verify_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_diff)


def response(status=200, body="<main>OK</main>", media="text/html"):
    return {"status": status, "body": body, "content_type": media, "location": None}


class VerifyDifferentialTests(unittest.TestCase):
    @patch.object(verify_diff, "fetch_endpoint")
    def test_dynamic_token_is_masked_but_html_structure_is_not(self, fetch):
        fetch.side_effect = [
            response(body='<main><input name="authenticity_token" value="one"><p>OK</p></main>'),
            response(body='<main><input name="authenticity_token" value="two"><p>OK</p></main>'),
        ]
        result = verify_diff.compare_endpoints("base", {"other": "candidate"}, [("/articles", "text/html")])
        self.assertTrue(result["all_eligible"])
        fetch.side_effect = [response(body="<main><p>OK</p></main>"), response(body="<main><b>OK</b></main>")]
        self.assertFalse(verify_diff.compare_endpoints("base", {"other": "candidate"}, [("/articles", "text/html")])["all_eligible"])

    @patch.object(verify_diff, "fetch_endpoint")
    def test_matching_error_status_cannot_pass(self, fetch):
        fetch.side_effect = [response(status=422, body="invalid A"), response(status=422, body="invalid A")]
        report = verify_diff.compare_endpoints("base", {"other": "candidate"}, [("/articles", "text/html")])
        self.assertFalse(report["all_eligible"])
        self.assertEqual(report["endpoints"][0]["candidates"]["other"]["verdict"], "MISMATCH")

    @patch.object(verify_diff, "fetch_endpoint")
    def test_media_type_and_json_payload_must_match(self, fetch):
        fetch.side_effect = [
            response(body='{"a":1}', media="application/json"),
            response(body='{"a":2}', media="application/json"),
        ]
        report = verify_diff.compare_endpoints("base", {"other": "candidate"}, [("/articles.json", "application/json")])
        self.assertFalse(report["all_eligible"])
        fetch.side_effect = [
            response(body='{"a":1}', media="application/json"),
            response(body='{"a":1}', media="text/html"),
        ]
        report = verify_diff.compare_endpoints("base", {"other": "candidate"}, [("/articles.json", "application/json")])
        self.assertEqual(report["endpoints"][0]["candidates"]["other"]["verdict"], "ERROR")

    def test_empty_candidate_is_rejected(self):
        with self.assertRaises(ValueError):
            verify_diff.compare_endpoints("base", {}, [("/articles", "text/html")])

    def test_markdown_columns(self):
        result = {"all_eligible": False, "endpoints": [{
            "path": "/articles", "accept": "text/html", "baseline_status": 200,
            "candidates": {"one": {"status": 200, "verdict": "MATCH"},
                           "two": {"status": 500, "verdict": "MISMATCH"}},
        }]}
        lines = verify_diff.format_markdown_report(result, ["one", "two"]).splitlines()
        columns = [line.count("|") for line in lines if line.startswith("|")]
        self.assertEqual(columns, [6, 6, 6])


if __name__ == "__main__":
    unittest.main()

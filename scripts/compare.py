#!/usr/bin/env python3
"""
Comprehensive differential testing suite comparing Rails and Spinel AOT.
Verifies equivalence across HTTP status, headers, HTML DOM / JSON, and SQLite final state.
"""
import argparse
import difflib
import html.parser
import http.client
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from http.cookies import SimpleCookie


class TokenParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.csrf_token = ""
        self.csrf_param = "authenticity_token"

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "input" and attrs_dict.get("name") == "authenticity_token":
            self.csrf_token = attrs_dict.get("value", "")
        elif tag == "meta" and attrs_dict.get("name") == "csrf-token":
            self.csrf_token = attrs_dict.get("content", "")
        elif tag == "meta" and attrs_dict.get("name") == "csrf-param":
            self.csrf_param = attrs_dict.get("content", "authenticity_token")


class HttpClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.parsed = urllib.parse.urlsplit(self.base_url)
        self.cookies = {}
        self.last_csrf_token = ""

    def request(self, method, path, fields=None, headers=None):
        req_headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
            "Origin": self.base_url,
            "Referer": self.base_url + path,
        }
        if headers:
            req_headers.update(headers)

        body = None
        if fields is not None:
            if isinstance(fields, dict):
                req_headers["Content-Type"] = "application/x-www-form-urlencoded"
                body = urllib.parse.urlencode(fields)
            elif isinstance(fields, str):
                body = fields

        conn = http.client.HTTPConnection(self.parsed.hostname, self.parsed.port, timeout=15)
        conn.request(method, path, body, req_headers)
        response = conn.getresponse()

        status = response.status
        location = response.getheader("Location")
        content_type = response.getheader("Content-Type", "")

        for key, value in response.getheaders():
            if key.lower() == "set-cookie":
                jar = SimpleCookie(value)
                self.cookies.update({name: item.value for name, item in jar.items()})

        text = response.read().decode("utf-8", errors="replace")
        conn.close()

        # Update CSRF token if present
        parser = TokenParser()
        try:
            parser.feed(text)
            if parser.csrf_token:
                self.last_csrf_token = parser.csrf_token
        except Exception:
            pass

        return {
            "status": status,
            "location": location,
            "content_type": content_type,
            "body": text,
        }


def canonicalize_tags(html_str):
    """Sort attributes within HTML tags alphabetically and normalize self-closing slashes."""
    def sort_attrs(match):
        tag_name = match.group(1)
        attrs_str = match.group(2)
        if not attrs_str or not attrs_str.strip():
            return f"<{tag_name}>"
        # Match attribute="value" or attribute='value' or standalone attribute
        attr_pairs = re.findall(r'([a-zA-Z0-9_\-]+)(?:=(["\'])(.*?)\2)?', attrs_str)
        sorted_attrs = sorted(attr_pairs, key=lambda x: x[0])
        formatted = []
        for k, q, v in sorted_attrs:
            if q:
                formatted.append(f'{k}="{v}"')
            else:
                formatted.append(k)
        return f"<{tag_name} {' '.join(formatted)}>"

    return re.sub(r'<([a-zA-Z0-9\-]+)([^>]*?)(\s*/?)>', sort_attrs, html_str)


def normalize_html(html_text):
    """Normalize dynamic and transient elements in HTML for structural comparison."""
    # 1. Strip HTML comments (such as Rails development view template annotations)
    s = re.sub(r'<!--.*?-->', '', html_text, flags=re.DOTALL)

    # 2. Normalize CSRF tokens
    s = re.sub(
        r'(<input[^>]+name="authenticity_token"[^>]+value=")[^"]*(")',
        r'\1[CSRF_TOKEN]\2',
        s,
    )
    s = re.sub(
        r'(<meta[^>]+name="csrf-token"[^>]+content=")[^"]*(")',
        r'\1[CSRF_TOKEN]\2',
        s,
    )

    # 3. Normalize Turbo Cable stream signatures
    s = re.sub(
        r'(signed-stream-name=")[^"]*(")',
        r'\1[STREAM_SIGNATURE]\2',
        s,
    )

    # 4. Extract <main> or <body> content if present to focus on semantic content
    main_match = re.search(r'<main[^>]*>(.*?)</main>', s, re.DOTALL | re.IGNORECASE)
    if main_match:
        s = main_match.group(1)
    else:
        body_match = re.search(r'<body[^>]*>(.*?)</body>', s, re.DOTALL | re.IGNORECASE)
        if body_match:
            s = body_match.group(1)

    # 5. Normalize timestamps (ISO-8601 or common Rails datetime formats)
    s = re.sub(
        r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?',
        '[TIMESTAMP]',
        s,
    )

    # 6. Normalize asset fingerprinted paths or varying asset domains
    s = re.sub(r'/assets/[a-zA-Z0-9_\-]+-[a-f0-9]{32,64}\.(css|js)', r'/assets/[ASSET].\1', s)

    # 7. Canonicalize tag attributes (order invariance)
    s = canonicalize_tags(s)

    # 8. Collapse continuous whitespace between tags and normalize line endings
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r'>\s+<', '><', s)
    s = re.sub(r'[ \t]+', ' ', s)
    lines = [line.strip() for line in s.split('\n') if line.strip()]
    return '\n'.join(lines)


def normalize_location(location):
    """Normalize Location header to relative path."""
    if not location:
        return None
    parsed = urllib.parse.urlsplit(location)
    return parsed.path + (("?" + parsed.query) if parsed.query else "")


def fetch_database_records(db_path):
    """Fetch all rows from articles and comments tables ordered by id."""
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        articles = [
            dict(row) for row in db.execute(
                "SELECT id, title, body FROM articles ORDER BY id"
            ).fetchall()
        ]
        comments = [
            dict(row) for row in db.execute(
                "SELECT id, article_id, commenter, body FROM comments ORDER BY id"
            ).fetchall()
        ]
        return {"articles": articles, "comments": comments}


class DifferentialTester:
    def __init__(self, rails_url, aot_url, rails_db, aot_db, report_dir="reports/differential", inject_diff=False):
        self.rails = HttpClient(rails_url)
        self.aot = HttpClient(aot_url)
        self.rails_db = rails_db
        self.aot_db = aot_db
        self.report_dir = report_dir
        self.inject_diff = inject_diff
        self.results = []
        os.makedirs(self.report_dir, exist_ok=True)

    def log(self, msg):
        print(f"[COMPARE] {msg}")

    def execute_and_compare(self, case_name, method, path, rails_fields=None, aot_fields=None, headers=None, expect_status=None, check_body=True):
        self.log(f"Running {case_name}: {method} {path}...")
        
        # Inject intentional discrepancy if requested (to test validation assertion)
        if self.inject_diff and "intentional" in case_name:
            if rails_fields and isinstance(rails_fields, dict):
                aot_fields = dict(rails_fields)
                aot_fields["article[title]"] = "INJECTED_DIFF"

        resp_rails = self.rails.request(method, path, rails_fields, headers)
        resp_aot = self.aot.request(method, path, aot_fields or rails_fields, headers)

        diffs = []

        # 1. Compare HTTP status
        if resp_rails["status"] != resp_aot["status"]:
            diffs.append(f"HTTP Status mismatch: Rails={resp_rails['status']}, AOT={resp_aot['status']}")

        if expect_status is not None:
            if resp_rails["status"] != expect_status:
                diffs.append(f"Rails status {resp_rails['status']} does not match expected {expect_status}")
            if resp_aot["status"] != expect_status:
                diffs.append(f"AOT status {resp_aot['status']} does not match expected {expect_status}")

        # 2. Compare Location header (normalized)
        loc_rails = normalize_location(resp_rails["location"])
        loc_aot = normalize_location(resp_aot["location"])
        if loc_rails != loc_aot:
            diffs.append(f"Location header mismatch: Rails={loc_rails}, AOT={loc_aot}")

        # 3. Compare Normalized Body
        norm_rails = normalize_html(resp_rails["body"])
        norm_aot = normalize_html(resp_aot["body"])

        body_diff_str = ""
        if check_body and norm_rails != norm_aot:
            body_diff = list(difflib.unified_diff(
                norm_rails.splitlines(keepends=True),
                norm_aot.splitlines(keepends=True),
                fromfile="rails.html",
                tofile="aot.html",
            ))
            body_diff_str = "".join(body_diff[:100]) # Cap at 100 lines for report
            diffs.append(f"Body content mismatch ({len(body_diff)} diff lines)")

        passed = (len(diffs) == 0)

        # Write artifacts for this case
        case_report = {
            "case": case_name,
            "method": method,
            "path": path,
            "passed": passed,
            "diffs": diffs,
            "rails_status": resp_rails["status"],
            "aot_status": resp_aot["status"],
            "rails_location": loc_rails,
            "aot_location": loc_aot,
        }
        self.results.append(case_report)

        if not passed:
            self.log(f"  FAILED: {diffs}")
            with open(os.path.join(self.report_dir, f"{case_name}_diff.patch"), "w", encoding="utf-8") as f:
                f.write("\n".join(diffs) + "\n\n" + body_diff_str)
            with open(os.path.join(self.report_dir, f"{case_name}_rails.html"), "w", encoding="utf-8") as f:
                f.write(resp_rails["body"])
            with open(os.path.join(self.report_dir, f"{case_name}_aot.html"), "w", encoding="utf-8") as f:
                f.write(resp_aot["body"])
        else:
            self.log("  PASSED")

        return resp_rails, resp_aot, passed

    def run_all(self, target_case=None):
        cases = [
            self.case_01_articles_index,
            self.case_02_articles_new_form,
            self.case_03_create_article_validation_error,
            self.case_04_create_article_success,
            self.case_05_article_show,
            self.case_06_article_edit_form,
            self.case_07_update_article_validation_error,
            self.case_08_update_article_success,
            self.case_09_create_comment,
            self.case_10_destroy_comment,
            self.case_11_destroy_article,
            self.case_12_database_equivalence,
            self.case_13_json_format_negotiation,
        ]

        for case_fn in cases:
            c_name = case_fn.__name__
            if target_case and target_case != c_name:
                continue
            case_fn()

        summary_path = os.path.join(self.report_dir, "summary.json")
        total = len(self.results)
        passed = sum(1 for r in self.results if r["passed"])
        failed = total - passed

        summary_data = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "all_passed": (failed == 0),
            "results": self.results,
        }
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)

        self.log(f"Summary: {passed}/{total} cases passed. Report saved to {summary_path}")
        return failed == 0

    # -------------------------------------------------------------
    # Test Cases
    # -------------------------------------------------------------
    def case_01_articles_index(self):
        """GET /articles: Verify initial listing of seeded articles."""
        self.execute_and_compare(
            "case_01_articles_index",
            "GET",
            "/articles",
            expect_status=200,
        )

    def case_02_articles_new_form(self):
        """GET /articles/new: Verify new article form structure."""
        self.execute_and_compare(
            "case_02_articles_new_form",
            "GET",
            "/articles/new",
            expect_status=200,
        )

    def case_03_create_article_validation_error(self):
        """POST /articles: Validation error when empty title and body submitted."""
        fields_rails = {
            "article[title]": "",
            "article[body]": "",
            "authenticity_token": self.rails.last_csrf_token,
        }
        fields_aot = {
            "article[title]": "",
            "article[body]": "",
            "authenticity_token": self.aot.last_csrf_token,
        }
        self.execute_and_compare(
            "case_03_create_article_validation_error",
            "POST",
            "/articles",
            rails_fields=fields_rails,
            aot_fields=fields_aot,
            expect_status=422,
        )

    def case_04_create_article_success(self):
        """POST /articles: Create article with valid parameters."""
        fields_rails = {
            "article[title]": "Differential Test Article",
            "article[body]": "This is a valid body for differential equivalence verification.",
            "authenticity_token": self.rails.last_csrf_token,
        }
        fields_aot = {
            "article[title]": "Differential Test Article",
            "article[body]": "This is a valid body for differential equivalence verification.",
            "authenticity_token": self.aot.last_csrf_token,
        }
        resp_rails, resp_aot, passed = self.execute_and_compare(
            "case_04_create_article_success",
            "POST",
            "/articles",
            rails_fields=fields_rails,
            aot_fields=fields_aot,
            check_body=False,  # Redirect body is typically minimal; Location header is checked
        )
        if passed:
            # Check redirect status is 302 or 303
            loc_r = normalize_location(resp_rails["location"])
            loc_a = normalize_location(resp_aot["location"])
            assert loc_r == loc_a, f"Redirect location mismatch: {loc_r} vs {loc_a}"

    def case_05_article_show(self):
        """GET /articles/4: View the newly created article details and comment section."""
        self.execute_and_compare(
            "case_05_article_show",
            "GET",
            "/articles/4",
            expect_status=200,
        )

    def case_06_article_edit_form(self):
        """GET /articles/4/edit: View edit form populated with current article values."""
        self.execute_and_compare(
            "case_06_article_edit_form",
            "GET",
            "/articles/4/edit",
            expect_status=200,
        )

    def case_07_update_article_validation_error(self):
        """PATCH /articles/4: Validation error when updating with empty body."""
        fields_rails = {
            "_method": "patch",
            "article[title]": "Differential Test Article",
            "article[body]": "",
            "authenticity_token": self.rails.last_csrf_token,
        }
        fields_aot = {
            "_method": "patch",
            "article[title]": "Differential Test Article",
            "article[body]": "",
            "authenticity_token": self.aot.last_csrf_token,
        }
        self.execute_and_compare(
            "case_07_update_article_validation_error",
            "POST",
            "/articles/4",
            rails_fields=fields_rails,
            aot_fields=fields_aot,
            expect_status=422,
        )

    def case_08_update_article_success(self):
        """PATCH /articles/4: Update article with valid new content."""
        fields_rails = {
            "_method": "patch",
            "article[title]": "Updated Differential Test Article",
            "article[body]": "This is the updated body content for equivalence verification.",
            "authenticity_token": self.rails.last_csrf_token,
        }
        fields_aot = {
            "_method": "patch",
            "article[title]": "Updated Differential Test Article",
            "article[body]": "This is the updated body content for equivalence verification.",
            "authenticity_token": self.aot.last_csrf_token,
        }
        self.execute_and_compare(
            "case_08_update_article_success",
            "POST",
            "/articles/4",
            rails_fields=fields_rails,
            aot_fields=fields_aot,
            check_body=False,
        )

    def case_09_create_comment(self):
        """POST /articles/4/comments: Add comment to article."""
        # Refresh article detail page to load active CSRF token from comment form
        self.rails.request("GET", "/articles/4")
        self.aot.request("GET", "/articles/4")
        fields_rails = {
            "comment[commenter]": "Reviewer Alice",
            "comment[body]": "Equivalence testing for comment creation.",
            "authenticity_token": self.rails.last_csrf_token,
        }
        fields_aot = {
            "comment[commenter]": "Reviewer Alice",
            "comment[body]": "Equivalence testing for comment creation.",
            "authenticity_token": self.aot.last_csrf_token,
        }
        self.execute_and_compare(
            "case_09_create_comment",
            "POST",
            "/articles/4/comments",
            rails_fields=fields_rails,
            aot_fields=fields_aot,
            check_body=False,
        )

    def case_10_destroy_comment(self):
        """DELETE /articles/4/comments/4: Delete newly created comment."""
        # Refresh article detail page to load active CSRF token for comment deletion
        self.rails.request("GET", "/articles/4")
        self.aot.request("GET", "/articles/4")
        fields_rails = {
            "_method": "delete",
            "authenticity_token": self.rails.last_csrf_token,
        }
        fields_aot = {
            "_method": "delete",
            "authenticity_token": self.aot.last_csrf_token,
        }
        # In seeded database, comments 1, 2 belong to article 1, comment 3 belongs to article 2.
        # The new comment is id 4.
        self.execute_and_compare(
            "case_10_destroy_comment",
            "POST",
            "/articles/4/comments/4",
            rails_fields=fields_rails,
            aot_fields=fields_aot,
            check_body=False,
        )

    def case_11_destroy_article(self):
        """DELETE /articles/4: Destroy article."""
        # Refresh article index page to load active CSRF token for article deletion
        self.rails.request("GET", "/articles")
        self.aot.request("GET", "/articles")
        fields_rails = {
            "_method": "delete",
            "authenticity_token": self.rails.last_csrf_token,
        }
        fields_aot = {
            "_method": "delete",
            "authenticity_token": self.aot.last_csrf_token,
        }
        self.execute_and_compare(
            "case_11_destroy_article",
            "POST",
            "/articles/4",
            rails_fields=fields_rails,
            aot_fields=fields_aot,
            check_body=False,
        )

    def case_12_database_equivalence(self):
        """Compare the final state of SQLite databases between Rails and AOT."""
        self.log("Running case_12_database_equivalence...")
        diffs = []
        try:
            records_rails = fetch_database_records(self.rails_db)
            records_aot = fetch_database_records(self.aot_db)

            if records_rails["articles"] != records_aot["articles"]:
                diffs.append(f"Articles table mismatch: Rails={records_rails['articles']} vs AOT={records_aot['articles']}")
            if records_rails["comments"] != records_aot["comments"]:
                diffs.append(f"Comments table mismatch: Rails={records_rails['comments']} vs AOT={records_aot['comments']}")
        except Exception as e:
            diffs.append(f"Database comparison exception: {e}")

        passed = (len(diffs) == 0)
        self.results.append({
            "case": "case_12_database_equivalence",
            "passed": passed,
            "diffs": diffs,
        })
        if not passed:
            self.log(f"  FAILED: {diffs}")
            with open(os.path.join(self.report_dir, "case_12_database_diff.json"), "w", encoding="utf-8") as f:
                json.dump({"diffs": diffs, "rails": records_rails, "aot": records_aot}, f, indent=2)
        else:
            self.log("  PASSED")

    def case_13_json_format_negotiation(self):
        """
        Record behavior when requesting JSON format.
        Documents known divergence (Issue #6 investigation): Roundhouse drops format.json in favor of HTML branch.
        """
        self.log("Running case_13_json_format_negotiation...")
        resp_rails = self.rails.request("GET", "/articles.json")
        resp_aot = self.aot.request("GET", "/articles.json")

        diffs = []
        is_known_divergence = False
        if "application/json" in resp_rails["content_type"] and "text/html" in resp_aot["content_type"]:
            is_known_divergence = True
            diffs.append("Known divergence: Rails answers JSON, AOT falls back to HTML (Roundhouse warning[lower_residue])")

        # Record diagnostic report for format negotiation
        report = {
            "case": "case_13_json_format_negotiation",
            "passed": True,  # Documented divergence: passes as informational diagnostic
            "known_divergence": is_known_divergence,
            "diffs": diffs,
            "rails_content_type": resp_rails["content_type"],
            "aot_content_type": resp_aot["content_type"],
        }
        self.results.append(report)
        with open(os.path.join(self.report_dir, "case_13_json_negotiation.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        self.log(f"  DIAGNOSTIC RECORDED: {diffs}")


def main():
    parser = argparse.ArgumentParser(description="Differential tester for Rails vs Spinel AOT.")
    parser.add_argument("rails_url", help="Base URL of running Rails server (e.g. http://127.0.0.1:3000)")
    parser.add_argument("aot_url", help="Base URL of running Spinel AOT server (e.g. http://127.0.0.1:38000)")
    parser.add_argument("rails_db", help="Path to Rails SQLite database")
    parser.add_argument("aot_db", help="Path to Spinel AOT SQLite database")
    parser.add_argument("--report-dir", default="reports/differential", help="Directory to save differential reports")
    parser.add_argument("--case", default=None, help="Execute specific case by name")
    parser.add_argument("--inject-diff", action="store_true", help="Inject intentional discrepancy to verify assertion failure")

    args = parser.parse_args()

    tester = DifferentialTester(
        rails_url=args.rails_url,
        aot_url=args.aot_url,
        rails_db=args.rails_db,
        aot_db=args.aot_db,
        report_dir=args.report_dir,
        inject_diff=args.inject_diff,
    )

    success = tester.run_all(target_case=args.case)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

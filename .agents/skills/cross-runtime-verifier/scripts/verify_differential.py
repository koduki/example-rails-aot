#!/usr/bin/env python3
"""
verify_differential.py - Semantic Differential Testing Helper for Multi-Runtime Systems.

Compares HTTP responses between a baseline service (e.g., standard Rails)
and candidate runtimes (e.g., JRuby, Spinel AOT) across standard endpoints.
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from typing import Dict, Any, List, Tuple


def normalize_html(body: str) -> str:
    """Strip dynamic CSRF meta tokens, timestamps, and redundant whitespace."""
    # Remove CSRF meta tags
    body = re.sub(r'<meta\s+name=["\']csrf-token["\'].*?>', '', body, flags=re.IGNORECASE)
    body = re.sub(r'<meta\s+name=["\']csrf-param["\'].*?>', '', body, flags=re.IGNORECASE)
    # Collapse multiple whitespaces and newlines
    body = re.sub(r'\s+', ' ', body)
    return body.strip()


def normalize_json(body: str) -> Any:
    """Parse JSON and normalize formatting."""
    try:
        data = json.loads(body)
        return json.dumps(data, sort_keys=True)
    except Exception:
        return body.strip()


def fetch_endpoint(base_url: str, path: str, accept_header: str) -> Tuple[int, str]:
    """Fetch response status and body from a given URL."""
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": accept_header, "User-Agent": "CrossRuntimeVerifier/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            raw_body = resp.read().decode('utf-8', errors='replace')
            return status, raw_body
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode('utf-8', errors='replace') if e.fp else ""
        return e.code, raw_body
    except Exception as e:
        return 0, str(e)


def compare_endpoints(baseline_url: str, candidate_urls: Dict[str, str], endpoints: List[Dict[str, str]]) -> Dict[str, Any]:
    """Execute differential comparison matrix across all endpoints."""
    results = []
    all_eligible = True

    for ep in endpoints:
        path = ep["path"]
        accept = ep.get("accept", "text/html")
        ep_type = "json" if "json" in accept or path.endswith(".json") else "html"

        b_status, b_body = fetch_endpoint(baseline_url, path, accept)
        b_norm = normalize_json(b_body) if ep_type == "json" else normalize_html(b_body)

        row = {
            "path": path,
            "accept": accept,
            "baseline_status": b_status,
            "candidates": {}
        }

        for name, cand_url in candidate_urls.items():
            c_status, c_body = fetch_endpoint(cand_url, path, accept)
            c_norm = normalize_json(c_body) if ep_type == "json" else normalize_html(c_body)

            status_match = (b_status == c_status)
            body_match = (b_norm == c_norm) if b_status == 200 else (b_status == c_status)

            verdict = "MATCH" if (status_match and body_match) else "MISMATCH"
            if b_status == 0 or c_status == 0:
                verdict = "ERROR"
            if verdict != "MATCH":
                all_eligible = False

            row["candidates"][name] = {
                "status": c_status,
                "status_match": status_match,
                "body_match": body_match,
                "verdict": verdict
            }

        results.append(row)

    return {"all_eligible": all_eligible, "endpoints": results}


def format_markdown_report(report: Dict[str, Any], baseline_name: str, candidate_names: List[str]) -> str:
    """Format differential verification results as a clean Markdown table."""
    lines = [
        f"## Preflight Differential Testing Report",
        f"",
        f"- **Baseline Reference**: {baseline_name}",
        f"- **Overall Eligibility**: {'✅ ALL ELIGIBLE' if report['all_eligible'] else '⚠️ BLOCKED OR DIVERGENT'}",
        f"",
        "| Endpoint | Accept | Baseline Status | " + " | ".join([f"{name} Verdict" for name in candidate_names]) + " |",
        "|---|---|:---:|" + ":---:|".join(["---" for _ in candidate_names]) + ":---:|"
    ]

    for ep in report["endpoints"]:
        cand_cols = []
        for name in candidate_names:
            c_info = ep["candidates"].get(name, {})
            v = c_info.get("verdict", "UNKNOWN")
            symbol = "✅ MATCH" if v == "MATCH" else ("❌ MISMATCH" if v == "MISMATCH" else "💥 ERROR")
            cand_cols.append(f"{symbol} ({c_info.get('status', 'N/A')})")

        lines.append(f"| `{ep['path']}` | `{ep['accept']}` | {ep['baseline_status']} | " + " | ".join(cand_cols) + " |")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Cross-Runtime Differential Verifier")
    parser.add_argument("--baseline", required=True, help="Baseline service URL (e.g. http://127.0.0.1:3000)")
    parser.add_argument("--candidate", action="append", required=True, help="Candidate in name=url format (e.g. spinel=http://127.0.0.1:3002)")
    args = parser.parse_args()

    candidates = {}
    for c in args.candidate:
        parts = c.split("=", 1)
        if len(parts) == 2:
            candidates[parts[0]] = parts[1]

    standard_endpoints = [
        {"path": "/articles", "accept": "text/html"},
        {"path": "/articles/1", "accept": "text/html"},
        {"path": "/articles/new", "accept": "text/html"},
        {"path": "/articles.json", "accept": "application/json"},
        {"path": "/articles/1.json", "accept": "application/json"},
    ]

    report = compare_endpoints(args.baseline, candidates, standard_endpoints)
    md = format_markdown_report(report, "baseline", list(candidates.keys()))
    print(md)

    if not report["all_eligible"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

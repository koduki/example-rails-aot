#!/usr/bin/env python3
"""Read-only HTTP screen for the five article endpoints.

The benchmark's authoritative preflight also tests mutations and database state;
this helper deliberately shares its response canonicalization.
"""

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from scripts.bench.preflight import canonical  # noqa: E402


ENDPOINTS = [
    ("/articles", "text/html"),
    ("/articles/1", "text/html"),
    ("/articles/new", "text/html"),
    ("/articles.json", "application/json"),
    ("/articles/1.json", "application/json"),
]


def fetch_endpoint(base_url, path, accept):
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": accept, "User-Agent": "CrossRuntimeVerifier/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "location": resp.headers.get("Location"),
                "body": resp.read().decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as error:
        return {
            "status": error.code,
            "content_type": error.headers.get("Content-Type", ""),
            "location": error.headers.get("Location"),
            "body": error.read().decode("utf-8", errors="replace"),
        }
    except (OSError, ValueError) as error:
        return {"status": 0, "error": str(error)}


def compare_endpoints(baseline_url, candidate_urls, endpoints=ENDPOINTS):
    if not candidate_urls or not endpoints:
        raise ValueError("At least one candidate and endpoint are required")
    rows = []
    eligible = True
    for path, accept in endpoints:
        baseline = fetch_endpoint(baseline_url, path, accept)
        row = {"path": path, "accept": accept, "baseline_status": baseline["status"], "candidates": {}}
        for name, url in candidate_urls.items():
            candidate = fetch_endpoint(url, path, accept)
            detail = ""
            if baseline["status"] == 0 or candidate["status"] == 0:
                verdict = "ERROR"
                detail = baseline.get("error", "") or candidate.get("error", "")
            else:
                try:
                    left = canonical(baseline, expect_json=accept == "application/json")
                    right = canonical(candidate, expect_json=accept == "application/json")
                    # A matching error page is not a successfully served read endpoint.
                    verdict = "MATCH" if left == right and baseline["status"] == 200 else "MISMATCH"
                except (ValueError, TypeError) as error:
                    verdict = "ERROR"
                    detail = str(error)
            eligible &= verdict == "MATCH"
            row["candidates"][name] = {"status": candidate["status"], "verdict": verdict, "detail": detail}
        rows.append(row)
    return {"all_eligible": bool(eligible), "endpoints": rows}


def format_markdown_report(report, candidate_names):
    header = ["Endpoint", "Accept", "Baseline status"] + [f"{name} verdict" for name in candidate_names]
    lines = [
        "## Read-only HTTP differential screen",
        "",
        f"- **Result**: {'PASS' if report['all_eligible'] else 'BLOCKED'}",
        "- **Scope**: Five GET responses only; run `scripts/bench/preflight.py` for benchmark eligibility and database checks.",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in report["endpoints"]:
        values = [f"`{row['path']}`", f"`{row['accept']}`", str(row["baseline_status"])]
        for name in candidate_names:
            item = row["candidates"][name]
            values.append(f"{item['verdict']} ({item['status']})")
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", action="append", required=True, metavar="NAME=URL")
    args = parser.parse_args()
    candidates = {}
    for value in args.candidate:
        name, separator, url = value.partition("=")
        if not separator or not name.strip() or not url.startswith(("http://", "https://")) or name in candidates:
            parser.error(f"Invalid or duplicate candidate: {value!r}; expected unique NAME=http(s)://URL")
        candidates[name] = url
    if not args.baseline.startswith(("http://", "https://")):
        parser.error("--baseline must be an HTTP(S) URL")
    result = compare_endpoints(args.baseline, candidates)
    print(format_markdown_report(result, list(candidates)))
    if not result["all_eligible"]:
        sys.exit(1)


if __name__ == "__main__":
    main()

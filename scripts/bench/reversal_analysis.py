#!/usr/bin/env python3
"""Processing stage decomposition and reversal analysis: Rails vs Roundhouse.

Investigates throughput and latency reversals between Rails and Roundhouse
across workload scales (Actions smoke 3 articles vs C3 1,000 articles),
analyzing processing stages (SQL, instantiation, association preloading,
rendering, HTTP) and evaluating the impact of DB-level pagination.
"""

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional


def compute_ratio(num: Optional[float], denom: Optional[float]) -> Optional[float]:
    """Compute ratio safely, returning None if missing, invalid, or non-positive."""
    if num is None or denom is None:
        return None
    try:
        num_f, denom_f = float(num), float(denom)
        if denom_f <= 0.0 or math.isnan(num_f) or math.isnan(denom_f):
            return None
        return round(num_f / denom_f, 3)
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def calculate_dispersion(values: List[float]) -> Dict[str, Any]:
    """Calculate descriptive statistics and coefficient of variation (CV)."""
    if not values:
        return {
            "count": 0,
            "median": None,
            "mean": None,
            "min": None,
            "max": None,
            "stdev": None,
            "cv_pct": None,
        }
    n = len(values)
    med = statistics.median(values)
    mean_val = statistics.mean(values)
    min_val = min(values)
    max_val = max(values)
    stdev_val = statistics.stdev(values) if n > 1 else 0.0
    cv_pct = (stdev_val / mean_val * 100.0) if mean_val > 0 else 0.0
    return {
        "count": n,
        "median": round(med, 2),
        "mean": round(mean_val, 2),
        "min": round(min_val, 2),
        "max": round(max_val, 2),
        "stdev": round(stdev_val, 2),
        "cv_pct": round(cv_pct, 2),
    }


def analyze_warmup_repetitions(
    trials_dir: Path, targets: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Aggregate the final 4-window warmup RPS across repetitions for given targets."""
    results = {}
    for tgt in targets:
        results[tgt] = {"repetitions": [], "medians": []}

    if not trials_dir.is_dir():
        return results

    for d in sorted(trials_dir.iterdir()):
        if not d.is_dir():
            continue
        for tgt in targets:
            if d.name.endswith(tgt):
                wfile = d / "warmup.json"
                if not wfile.exists():
                    wfile = d / "data" / "warmup.json"
                if not wfile.exists():
                    continue
                try:
                    windows = json.loads(wfile.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not windows:
                    continue
                last4 = windows[-4:] if len(windows) >= 4 else windows
                rps_list = [
                    w.get("rps_successful") or w.get("rps")
                    for w in last4
                    if (w.get("rps_successful") or w.get("rps")) is not None
                ]
                if not rps_list:
                    continue
                rep_med = statistics.median(rps_list)
                results[tgt]["repetitions"].append(
                    {
                        "trial_dir": d.name,
                        "windows_count": len(windows),
                        "last4_rps": [round(x, 2) for x in rps_list],
                        "median_rps": round(rep_med, 2),
                    }
                )
                results[tgt]["medians"].append(rep_med)

    for tgt in targets:
        disp = calculate_dispersion(results[tgt]["medians"])
        results[tgt]["stats"] = disp

    return results


def analyze_fixed_100_rps(
    trials_dir: Path, targets: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Extract metrics from 100 RPS offered rate steps."""
    out = {tgt: [] for tgt in targets}
    if not trials_dir.is_dir():
        return out

    for d in sorted(trials_dir.iterdir()):
        if not d.is_dir():
            continue
        for tgt in targets:
            if d.name.endswith(tgt):
                for coarse_dir in sorted(d.iterdir()):
                    if not coarse_dir.is_dir() or "warmup" in coarse_dir.name:
                        continue
                    k6_file = coarse_dir / "k6-summary.json"
                    app_file = coarse_dir / "app-telemetry.json"
                    if k6_file.exists() and app_file.exists():
                        try:
                            k6 = json.loads(k6_file.read_text(encoding="utf-8"))
                            app = json.loads(app_file.read_text(encoding="utf-8"))
                        except Exception:
                            continue

                        if k6.get('driver') != 'k6-open-arrival' or k6.get('rate_offered') != 100:
                            continue
                        peak_mem_b = app.get("summary", {}).get("peak_container_memory_bytes")

                        out[tgt].append(
                            {
                                "trial_dir": d.name,
                                "phase": coarse_dir.name,
                                "iterations_dropped": k6.get("iterations_dropped"),
                                "offered_rate": k6.get("rate_offered", 100),
                                "duration_configured": k6.get("duration_configured"),
                                "requests_total": k6.get("requests_total"),
                                "requests_successful": k6.get("requests_successful"),
                                "errors": k6.get("requests_failed", k6.get("errors")),
                                "client_saturated": k6.get("client_saturated", False),
                                "rps": round(k6.get("rps", 0.0), 2),
                                "latency_p50_ms": k6.get("latency_ms", {}).get("p50"),
                                "latency_p90_ms": k6.get("latency_ms", {}).get("p90"),
                                "latency_p95_ms": k6.get("latency_ms", {}).get("p95"),
                                "latency_p99_ms": k6.get("latency_ms", {}).get("p99"),
                                "mean_cpu_pct": app.get("summary", {}).get("mean_cpu_pct"),
                                "peak_container_memory_bytes": peak_mem_b,
                                "peak_container_memory_mib": (
                                    round(peak_mem_b / (1024 * 1024), 1)
                                    if peak_mem_b is not None
                                    else None
                                ),
                            }
                        )
    return out


def evaluate_stage_costs(n_articles: int, m_comments: int) -> Dict[str, Any]:
    """Estimate theoretical operation counts and complexity across processing stages."""
    # Hypothetical algorithms, not inspected serving code or measured timings.
    # Rails: O(N+M) preloader via Hash lookup
    # Roundhouse: O(N*M) nested loop preloader
    rails_preloader_ops = n_articles + m_comments
    roundhouse_preloader_ops = n_articles * m_comments

    # DB Paged (LIMIT 20)
    db_paged_n = min(20, n_articles)
    db_paged_m = min(20, m_comments)
    db_paged_preloader_ops = db_paged_n * db_paged_m

    reduction_factor = (
        round(roundhouse_preloader_ops / db_paged_preloader_ops, 1)
        if db_paged_preloader_ops > 0
        else None
    )

    return {
        "evidence_type": "hypothetical_operation_model",
        "measured_stage_timings": False,
        "workload": {
            "n_articles": n_articles,
            "m_comments": m_comments,
        },
        "stage_operations": {
            "sql_rows_scanned": n_articles,
            "sql_rows_loaded": n_articles,
            "articles_instantiated": n_articles,
            "comments_instantiated": m_comments,
            "rails_preloader_hash_ops": rails_preloader_ops,
            "roundhouse_preloader_loop_iterations": roundhouse_preloader_ops,
            "db_paged_articles_loaded": db_paged_n,
            "db_paged_comments_loaded": db_paged_m,
            "db_paged_preloader_loop_iterations": db_paged_preloader_ops,
            "db_paged_loop_reduction_factor": reduction_factor,
        },
    }


def generate_reversal_report(warmup_data, fixed_100_data, commit_sha='unknown'):
    """Render only supplied observations; no baked-in results or causal claims."""
    lines = ['# Rails vs Roundhouse Performance Reversal Diagnostic', '',
             f'- Analyzed commit: `{commit_sha}`',
             '- Warmup is closed-loop diagnostic throughput, not sustained capacity.',
             '- Actions smoke and C3, app-sliced and DB-paged workloads are separate cohorts.', '',
             '## Warmup observations', '',
             '| Target | Trial directory | Final-window median RPS | Window count |',
             '| --- | --- | ---: | ---: |']
    for target, data in warmup_data.items():
        for rep in data.get('repetitions', []):
            lines.append(f'| `{target}` | `{rep.get("trial_dir", "unknown")}` | {rep["median_rps"]} | {rep.get("windows_count", "unknown")} |')
    lines += ['', '## Fixed 100 RPS observations', '',
              'Every selected step is listed, including failures. Compare only aligned duration/phase/repetition.',
              'Memory is `peak_container_memory_bytes` / 2^20 (MiB); unavailable telemetry stays missing.', '',
              '| Target | Trial | Duration | Phase | Requests | Failed | Drops | p99 ms | CPU % | Memory MiB |',
              '| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for target, steps in fixed_100_data.items():
        for step in steps:
            values = [step.get(k, 'unknown') for k in ('trial_dir', 'duration_configured', 'phase',
                       'requests_total', 'errors', 'iterations_dropped', 'latency_p99_ms',
                       'mean_cpu_pct', 'peak_container_memory_mib')]
            lines.append(f'| `{target}` | ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', '## Processing stages and next experiment', '',
              'No stage timings or microbenchmark results are supplied to this generator. SQL, materialization, association preload, render and HTTP wait remain competing hypotheses.',
              'The optional operation-count model is hypothetical; confirm the generated serving code and trace each stage before attributing the bottleneck.',
              'Run same-load repetitions with stage timers, allocation/GC deltas, and JVM/CPU/wait traces. Publish code, commands and raw samples for any microbenchmark.', '',
              '## Capacity validity', '',
              'No capacity ratios are inferred from warmup or selected fixed-rate steps. Use the full capacity report and its per-repetition intervals.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Rails vs Roundhouse performance reversal across processing stages."
    )
    parser.add_argument(
        "--results-dir",
        default="bench-results/gce-20260928-c3-capacity",
        help="Path to GCE capacity benchmark results directory",
    )
    parser.add_argument(
        "--output-md",
        default="docs/reversal-analysis-rails-vs-roundhouse.md",
        help="Output Markdown report path",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional output JSON path",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    trials_dir = results_dir / "trials"
    targets = [
        "rails-cruby-off",
        "rails-cruby-yjit",
        "emit-cruby-off",
        "emit-cruby-yjit",
    ]

    warmup_data = analyze_warmup_repetitions(trials_dir, targets)
    fixed_100_data = analyze_fixed_100_rps(trials_dir, targets)
    stage_eval = evaluate_stage_costs(n_articles=1000, m_comments=1000)

    env_path = results_dir / 'env.json'
    commit = json.loads(env_path.read_text(encoding='utf-8')).get('git_commit', 'unknown') if env_path.exists() else 'unknown'
    report_md = generate_reversal_report(warmup_data, fixed_100_data, commit)

    output_md_path = Path(args.output_md)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.write_text(report_md, encoding="utf-8")
    print(f"Wrote Markdown report to {output_md_path}")

    if args.output_json:
        json_payload = {
            "warmup_repetitions": warmup_data,
            "fixed_100_rps": fixed_100_data,
            "stage_evaluation": stage_eval,
        }
        output_json_path = Path(args.output_json)
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(json_payload, indent=2), encoding="utf-8"
        )
        print(f"Wrote JSON report to {output_json_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Parse and report follow-up experiment results from a root benchmark directory."""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def parse_matched_rate(run_dir: Path):
    per_run_file = run_dir / "matched" / "matched-rate" / "trials" / "per-run.json"
    if not per_run_file.exists():
        return None
    data = json.loads(per_run_file.read_text(encoding="utf-8"))
    by_target = defaultdict(list)
    for r in data:
        by_target[r["target"]].append(r)

    summary = {}
    for target, reps in sorted(by_target.items()):
        passed = [r for r in reps if r.get("status") in ("passed", "completed")]
        p50s = [r["measurement"]["latency_ms"]["p50"] for r in passed if "measurement" in r and "latency_ms" in r["measurement"]]
        p99s = [r["measurement"]["latency_ms"]["p99"] for r in passed if "measurement" in r and "latency_ms" in r["measurement"]]
        cpus = [(r.get("telemetry") or {}).get("summary", {}).get("mean_cpu_pct", 0) for r in passed]
        mems = [((r.get("telemetry") or {}).get("summary", {}).get("peak_container_memory_bytes") or 0) / (1024 * 1024) for r in passed]
        summary[target] = {
            "total": len(reps),
            "completed": len(passed),
            "p50_median": sorted(p50s)[len(p50s) // 2] if p50s else None,
            "p99_median": sorted(p99s)[len(p99s) // 2] if p99s else None,
            "cpu_median": sorted(cpus)[len(cpus) // 2] if cpus else None,
            "mem_median": sorted(mems)[len(mems) // 2] if mems else None,
        }
    return summary


def parse_capacity_cohort(run_dir: Path, experiment: str):
    per_run_file = run_dir / experiment / experiment / "trials" / "per-run.json"
    if not per_run_file.exists():
        return None
    data = json.loads(per_run_file.read_text(encoding="utf-8"))
    by_target = defaultdict(list)
    for r in data:
        by_target[r["target"]].append(r)

    summary = {}
    for target, reps in sorted(by_target.items()):
        passed = [r for r in reps if r.get("status") in ("passed", "completed") and r.get("capacity_rps") is not None]
        caps = [r["capacity_rps"] for r in passed]
        warmups = [r.get("warmup_seconds", 0) for r in reps]
        caps_sorted = sorted(caps)
        summary[target] = {
            "total": len(reps),
            "completed": len(passed),
            "capacities": caps,
            "capacity_median": caps_sorted[len(caps_sorted) // 2] if caps_sorted else None,
            "min": min(caps) if caps else None,
            "max": max(caps) if caps else None,
            "warmup_mean": sum(warmups) / len(warmups) if warmups else None,
        }
    return summary


def parse_spinel_connections(run_dir: Path):
    results_file = run_dir / "spinel-connections" / "retest-results.json"
    if not results_file.exists():
        return None
    cells = json.loads(results_file.read_text(encoding="utf-8"))
    summary = {}
    for c in cells:
        cid = c["id"]
        per_run_file = run_dir / "spinel-connections" / cid / "trials" / "per-run.json"
        if not per_run_file.exists():
            continue
        trials = json.loads(per_run_file.read_text(encoding="utf-8"))
        passed = [t for t in trials if t.get("status") in ("passed", "completed")]
        p50s = [t["measurement"]["latency_ms"]["p50"] for t in passed if "measurement" in t and "latency_ms" in t["measurement"]]
        p99s = [t["measurement"]["latency_ms"]["p99"] for t in passed if "measurement" in t and "latency_ms" in t["measurement"]]
        errs = sum((t.get("measurement") or {}).get("errors", 0) for t in trials)
        summary[cid] = {
            "status": c.get("status"),
            "exit_code": c.get("exit_code"),
            "total": len(trials),
            "completed": len(passed),
            "p50_median": sorted(p50s)[len(p50s) // 2] if p50s else None,
            "p99_median": sorted(p99s)[len(p99s) // 2] if p99s else None,
            "errors": errs,
        }
    return summary


def format_report(run_dir: Path) -> str:
    lines = []
    lines.append(f"# Follow-up Benchmark Summary for {run_dir.name}\n")

    matched = parse_matched_rate(run_dir)
    if matched:
        lines.append("## Matched-Rate 10 RPS Summary (Medians)")
        lines.append("| Target | Completed | Median p50 (ms) | Median p99 (ms) | Median App CPU (%) | Median Mem (MiB) |")
        lines.append("|---|---|---|---|---|---|")
        for target, s in sorted(matched.items()):
            p50 = f"{s['p50_median']:.2f}" if s['p50_median'] is not None else "N/A"
            p99 = f"{s['p99_median']:.2f}" if s['p99_median'] is not None else "N/A"
            cpu = f"{s['cpu_median']:.1f}%" if s['cpu_median'] is not None else "N/A"
            mem = f"{s['mem_median']:.1f}" if s['mem_median'] is not None else "N/A"
            lines.append(f"| {target} | {s['completed']}/{s['total']} | {p50} | {p99} | {cpu} | {mem} |")
        lines.append("")

    cruby = parse_capacity_cohort(run_dir, "capacity-cruby")
    if cruby:
        lines.append("## Capacity-CRuby Summary")
        lines.append("| Target | Completed | Median Capacity (RPS) | Min | Max |")
        lines.append("|---|---|---|---|---|")
        for target, s in sorted(cruby.items()):
            med = f"{s['capacity_median']:.2f}" if s['capacity_median'] is not None else "N/A"
            cmin = f"{s['min']:.2f}" if s['min'] is not None else "N/A"
            cmax = f"{s['max']:.2f}" if s['max'] is not None else "N/A"
            lines.append(f"| {target} | {s['completed']}/{s['total']} | {med} | {cmin} | {cmax} |")
        lines.append("")

    jruby = parse_capacity_cohort(run_dir, "capacity-jruby")
    if jruby:
        lines.append("## Capacity-JRuby Summary")
        lines.append("| Target | Completed | Median Capacity (RPS) | Min | Max | Warmup Mean (s) |")
        lines.append("|---|---|---|---|---|---|")
        for target, s in sorted(jruby.items()):
            med = f"{s['capacity_median']:.2f}" if s['capacity_median'] is not None else "N/A"
            cmin = f"{s['min']:.2f}" if s['min'] is not None else "N/A"
            cmax = f"{s['max']:.2f}" if s['max'] is not None else "N/A"
            w = f"{s['warmup_mean']:.1f}" if s['warmup_mean'] is not None else "N/A"
            lines.append(f"| {target} | {s['completed']}/{s['total']} | {med} | {cmin} | {cmax} | {w} |")
        lines.append("")

    spinel = parse_spinel_connections(run_dir)
    if spinel:
        lines.append("## Spinel-Connections Summary")
        lines.append("| Cell | Status | Completed | Median p50 (ms) | Median p99 (ms) | Total Errors |")
        lines.append("|---|---|---|---|---|---|")
        for cid, s in sorted(spinel.items()):
            p50 = f"{s['p50_median']:.2f}" if s['p50_median'] is not None else "N/A"
            p99 = f"{s['p99_median']:.2f}" if s['p99_median'] is not None else "N/A"
            lines.append(f"| {cid} | {s['status']}({s['exit_code']}) | {s['completed']}/{s['total']} | {p50} | {p99} | {s['errors']} |")
        lines.append("")

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="Path to benchmark root run directory")
    args = parser.parse_args(argv)
    if not args.run_dir.exists():
        parser.error(f"Run directory not found: {args.run_dir}")
    print(format_report(args.run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

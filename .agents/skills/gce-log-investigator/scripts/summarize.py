#!/usr/bin/env python3
"""Read-only summary of extracted GCE capacity artifacts (standard library only)."""

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path


def read_json(path):
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def compact(measurement):
    latency = measurement.get("latency_ms") or {}
    return {
        "requests_total": measurement.get("requests_total"),
        "requests_failed": measurement.get("requests_failed"),
        "rps_successful": measurement.get("rps_successful"),
        "p99_ms": latency.get("p99"),
        "iterations_dropped": measurement.get("iterations_dropped"),
        "tester_cpu_pct": measurement.get("tester_cpu_pct"),
        "tester_network_errors": measurement.get("tester_network_errors"),
        "client_saturated": measurement.get("client_saturated"),
        "elapsed": measurement.get("elapsed"),
        "vus_peak": measurement.get("vus_peak"),
    }


def inspect(root, target_filter=None):
    plan = read_json(root / "plan.json")
    env = read_json(root / "env.json")
    rows = read_json(root / "trials" / "per-run.json")
    if not isinstance(plan, dict) or not isinstance(env, dict) or not isinstance(rows, list):
        raise ValueError("Expected plan.json, env.json, and trials/per-run.json in the run directory")
    profile = plan.get("profile") or {}
    result = {
        "source": str(root),
        "provenance": {
            "git_commit": env.get("git_commit"),
            "profile_sha256": env.get("profile_sha256"),
            "workload_name": profile.get("workload_name"),
            "fixture_articles": profile.get("fixture_articles"),
            "warmup_vus": profile.get("connections", 32) if profile.get("warmup_closed_loop") else None,
            "measurement_seconds": profile.get("measurement_seconds"),
            "slo_p99_ms": profile.get("slo_p99_ms"),
            "max_error_rate": profile.get("max_error_rate"),
        },
        "trials": [],
        "targets": {},
        "warnings": [],
    }
    schedule = plan.get("schedule") or []
    if len(schedule) != len(rows):
        result["warnings"].append("plan schedule and per-run length differ")
    counts = collections.defaultdict(lambda: collections.Counter())
    for index, row in enumerate(rows):
        target, repetition = row.get("target"), row.get("repetition")
        if target_filter and target != target_filter:
            continue
        counts[target][row.get("status", "unknown")] += 1
        directory = root / "trials" / f"{index:04d}-{target}"
        scheduled = schedule[index] if index < len(schedule) else None
        if scheduled and (scheduled.get("target"), scheduled.get("repetition")) != (target, repetition):
            result["warnings"].append(f"index {index}: plan schedule and per-run differ")
        if not directory.is_dir():
            directory = None
        item = {
            "target": target,
            "repetition": repetition,
            "status": row.get("status"),
            "reason": row.get("reason"),
            "directory": str(directory.relative_to(root)) if directory else None,
            "missing": [],
        }
        if directory is None:
            item["missing"].append("trial directory")
            result["trials"].append(item)
            continue
        actual = read_json(directory / "trial.json")
        if actual is None:
            item["missing"].append("trial.json")
        elif actual.get("status") != row.get("status"):
            result["warnings"].append(f"{directory.name}: per-run/trial status differs")
        warmup_paths = sorted(directory.glob("warmup-*/k6-summary.json"))
        warmup = []
        for path in warmup_paths:
            measurement = read_json(path)
            warmup.append(compact(measurement))
        expected = read_json(directory / "warmup.json")
        if expected is None:
            item["missing"].append("warmup.json")
        elif isinstance(expected, list) and len(expected) != len(warmup):
            item["missing"].append(f"warmup windows: expected {len(expected)}, found {len(warmup)}")
        if not warmup:
            item["missing"].append("warmup-*/k6-summary.json")
        total = sum(m["requests_total"] or 0 for m in warmup)
        failed = sum(m["requests_failed"] or 0 for m in warmup)
        late = [m["rps_successful"] for m in warmup[-4:] if m["rps_successful"] is not None]
        item["warmup_closed_loop"] = {
            "windows": len(warmup),
            "windows_with_failures": sum((m["requests_failed"] or 0) > 0 for m in warmup),
            "requests_total": total,
            "requests_failed": failed,
            "failure_rate": failed / total if total else None,
            "late_four_success_rps_median": statistics.median(late) if len(late) == 4 else None,
            "late_four_complete": len(late) == 4,
            "configured_vus": profile.get("connections", 32) if profile.get("warmup_closed_loop") else None,
            "mode": "constant-vus" if profile.get("warmup_closed_loop") else "check current runner",
        }
        steps = []
        for path in sorted(directory.glob("[0-9][0-9][0-9]-*/k6-summary.json")):
            measurement = read_json(path)
            step = compact(measurement)
            step["phase_directory"] = str(path.parent.relative_to(root))
            step["rate_offered"] = measurement.get("rate_offered")
            steps.append(step)
        item["open_arrival_steps"] = steps
        confirmation = [s for s in steps if "confirm" in Path(s["phase_directory"]).name]
        valid_confirmation = any(
            s["elapsed"] is not None and s["elapsed"] >= profile.get("measurement_seconds", 120) - 2
            and s["requests_total"] and s["requests_failed"] is not None
            and s["requests_failed"] / s["requests_total"] < profile.get("max_error_rate", 0.001)
            and s["p99_ms"] is not None and s["p99_ms"] <= profile.get("slo_p99_ms", 100)
            and s["iterations_dropped"] == 0 and not s["client_saturated"]
            and not s["tester_network_errors"]
            and s["tester_cpu_pct"] is not None
            and s["tester_cpu_pct"] < profile.get("max_tester_cpu_pct", 85)
            and s["rate_offered"] is not None and s["rps_successful"] is not None
            and s["rps_successful"] >= 0.95 * s["rate_offered"]
            for s in confirmation
        )
        item["confirmed_capacity_rps"] = (row.get("capacity_rps") if
                                          row.get("status") == "passed" and valid_confirmation else None)
        if row.get("status") == "passed" and (row.get("capacity_rps") is None or not valid_confirmation):
            item["missing"].append("valid confirmation evidence")
        result["trials"].append(item)
    for target, status_counts in sorted(counts.items()):
        trials = [r for r in result["trials"] if r["target"] == target]
        warm = [r["warmup_closed_loop"] for r in trials if "warmup_closed_loop" in r]
        total = sum(w["requests_total"] for w in warm)
        failed = sum(w["requests_failed"] for w in warm)
        late = [w["late_four_success_rps_median"] for w in warm if w["late_four_complete"]]
        result["targets"][target] = {
            "trial_status_counts": dict(status_counts),
            "warmup_windows": sum(w["windows"] for w in warm),
            "warmup_windows_with_failures": sum(w["windows_with_failures"] for w in warm),
            "warmup_requests_total": total,
            "warmup_requests_failed": failed,
            "warmup_failure_rate": failed / total if total else None,
            "late_four_success_rps_median_of_repetitions": statistics.median(late) if late else None,
            "late_four_repetitions_available": len(late),
            "confirmed_repetitions": sum(r.get("confirmed_capacity_rps") is not None for r in trials),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Extracted GCE run directory")
    parser.add_argument("--target", help="Only report one target")
    args = parser.parse_args()
    try:
        report = inspect(args.run.resolve(), args.target)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

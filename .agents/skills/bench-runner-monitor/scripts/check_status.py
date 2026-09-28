#!/usr/bin/env python3
"""Benchmark runner monitor.

Inspects running or completed benchmark progress, active processes, Docker containers,
trial outcomes, and ETA calculations for local or GCE runner environments.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure UTF-8 output on all platforms (including Windows PowerShell cp932)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def find_repo_root() -> Path:
    p = Path.cwd().resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".git").exists() or (parent / ".agents").exists():
            return parent
    return p


def read_terraform_defaults(repo_root: Path) -> Dict[str, str]:
    tfstate = repo_root / "infra" / "terraform" / "terraform.tfstate"
    defaults = {
        "instance": "bench-app-c3",
        "loadgen": "bench-loadgen-c3",
        "zone": "asia-northeast1-b",
        "project": "",
    }
    if not tfstate.exists():
        return defaults

    try:
        with open(tfstate, "r", encoding="utf-8") as f:
            data = json.load(f)
        outputs = data.get("outputs", {})
        if "app_instance_name" in outputs:
            defaults["instance"] = outputs["app_instance_name"].get("value", defaults["instance"])
        if "loadgen_instance_name" in outputs:
            defaults["loadgen"] = outputs["loadgen_instance_name"].get("value", defaults["loadgen"])
        if "zone" in outputs:
            defaults["zone"] = outputs["zone"].get("value", defaults["zone"])
        for res in data.get("resources", []):
            if res.get("type") == "google_compute_instance":
                for inst in res.get("instances", []):
                    attrs = inst.get("attributes", {})
                    if "zone" in attrs:
                        defaults["zone"] = attrs["zone"]
                    if "project" in attrs:
                        defaults["project"] = attrs["project"]
    except Exception:
        pass
    return defaults


REMOTE_INSPECTOR_CODE = r'''
import os, glob, json, subprocess, time

def inspect():
    # Process check
    ps_out = subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout
    runner_proc = None
    driver_proc = None
    k6_proc = None
    for line in ps_out.splitlines():
        if "scripts/bench/run.py" in line and "python" in line and "grep" not in line:
            runner_proc = line.strip()
        if "k6 run" in line and "grep" not in line:
            k6_proc = line.strip()
        if "scripts/bench/driver.py" in line and "grep" not in line:
            driver_proc = line.strip()

    # Docker container check
    try:
        docker_out = subprocess.run(["docker", "ps", "--format", "{{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        docker_out = ""
    active_containers = []
    for line in docker_out.splitlines():
        if line.strip():
            parts = line.split("\t")
            active_containers.append({
                "id": parts[0],
                "image": parts[1] if len(parts) > 1 else "",
                "status": parts[2] if len(parts) > 2 else "",
                "name": parts[3] if len(parts) > 3 else ""
            })

    # Search for candidate result directories
    home_dir = os.path.expanduser("~")
    search_paths = [
        os.path.join(home_dir, "example-rails-aot", "bench-results", "*"),
        os.path.join(home_dir, "bench-results", "*"),
        "bench-results/*"
    ]
    candidates = []
    for p in search_paths:
        candidates.extend(glob.glob(p))

    target_dir = None
    candidates = sorted(list(set(candidates)), key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
    for c in candidates:
        if os.path.isdir(c) and (os.path.exists(os.path.join(c, "plan.json")) or os.path.exists(os.path.join(c, "trials"))):
            target_dir = os.path.abspath(c)
            break

    res = {
        "runner_active": runner_proc is not None,
        "runner_cmd": runner_proc,
        "driver_cmd": driver_proc,
        "k6_cmd": k6_proc,
        "active_containers": active_containers,
        "results_dir": target_dir,
        "timestamp": time.time()
    }

    if not target_dir:
        print(json.dumps(res))
        return

    plan_file = os.path.join(target_dir, "plan.json")
    if os.path.exists(plan_file):
        try:
            with open(plan_file, "r") as f:
                plan = json.load(f)
            profile = plan.get("profile", {})
            res["plan"] = {
                "total_scheduled": len(plan.get("schedule", [])),
                "targets": profile.get("targets", []),
                "endpoints": profile.get("endpoints", []),
                "repetitions": profile.get("repetitions", 0),
                "action": plan.get("action"),
                "capacity_search": profile.get("capacity_search", False)
            }
        except Exception as e:
            res["plan_error"] = str(e)

    trials_dir = os.path.join(target_dir, "trials")
    if os.path.exists(trials_dir):
        all_entries = sorted([os.path.join(trials_dir, d) for d in os.listdir(trials_dir) if os.path.isdir(os.path.join(trials_dir, d)) and d != "per-run.json"])
        completed_trials = []
        status_counts = {}
        first_mtime = None
        latest_mtime = None

        for td in all_entries:
            tfile = os.path.join(td, "trial.json")
            if os.path.exists(tfile):
                try:
                    with open(tfile, "r") as f:
                        tdata = json.load(f)
                    status = tdata.get("status", "unknown")
                    status_counts[status] = status_counts.get(status, 0) + 1
                    mtime = os.path.getmtime(tfile)
                    if first_mtime is None or mtime < first_mtime:
                        first_mtime = mtime
                    if latest_mtime is None or mtime > latest_mtime:
                        latest_mtime = mtime
                    completed_trials.append({
                        "name": os.path.basename(td),
                        "target": tdata.get("target"),
                        "endpoint": tdata.get("endpoint"),
                        "status": status,
                        "warmup_seconds": tdata.get("warmup_seconds"),
                        "mtime": mtime
                    })
                except Exception:
                    pass

        curr_trial = None
        if len(all_entries) > len(completed_trials):
            curr_trial = os.path.basename(all_entries[-1])

        if curr_trial:
            progress_file = os.path.join(trials_dir, curr_trial, "progress.json")
            if os.path.exists(progress_file):
                with open(progress_file) as f:
                    res["progress"] = json.load(f)
        if curr_trial:
            last_file = os.path.join(trials_dir, curr_trial, "last-step.json")
            if os.path.exists(last_file):
                with open(last_file) as f:
                    res["last_step"] = json.load(f)
        res["trials"] = {
            "total_directories": len(all_entries),
            "completed_count": len(completed_trials),
            "status_counts": status_counts,
            "latest_completed": completed_trials[-1]["name"] if completed_trials else None,
            "current_trial": curr_trial,
            "first_trial_mtime": first_mtime,
            "latest_trial_mtime": latest_mtime,
            "recent_completed": completed_trials[-5:] if completed_trials else []
        }

    print(json.dumps(res))

inspect()
'''


def collect_local(results_dir: Optional[str] = None) -> Dict[str, Any]:
    repo_root = find_repo_root()
    base_dir = Path(results_dir) if results_dir else (repo_root / "bench-results")
    if not base_dir.exists():
        return {"mode": "local", "error": f"Path not found: {base_dir}"}

    candidates = [p for p in base_dir.glob("*") if p.is_dir()]
    target_dir: Optional[Path] = None
    if (base_dir / "plan.json").exists() or (base_dir / "trials").exists():
        target_dir = base_dir
    elif candidates:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for c in candidates:
            if (c / "plan.json").exists() or (c / "trials").exists():
                target_dir = c
                break

    res: Dict[str, Any] = {
        "mode": "local",
        "results_dir": str(target_dir) if target_dir else None,
        "timestamp": time.time(),
    }

    if not target_dir:
        return res

    plan_file = target_dir / "plan.json"
    if plan_file.exists():
        try:
            with open(plan_file, "r", encoding="utf-8") as f:
                plan = json.load(f)
            profile = plan.get("profile", {})
            res["plan"] = {
                "total_scheduled": len(plan.get("schedule", [])),
                "targets": profile.get("targets", []),
                "endpoints": profile.get("endpoints", []),
                "repetitions": profile.get("repetitions", 0),
                "capacity_search": profile.get("capacity_search", False),
            }
        except Exception as e:
            res["plan_error"] = str(e)

    trials_dir = target_dir / "trials"
    if trials_dir.exists():
        all_entries = sorted([d for d in trials_dir.iterdir() if d.is_dir() and d.name != "per-run.json"])
        completed_trials = []
        status_counts: Dict[str, int] = {}
        first_mtime = None
        latest_mtime = None

        for td in all_entries:
            tfile = td / "trial.json"
            if tfile.exists():
                try:
                    with open(tfile, "r", encoding="utf-8") as f:
                        tdata = json.load(f)
                    status = tdata.get("status", "unknown")
                    status_counts[status] = status_counts.get(status, 0) + 1
                    mtime = tfile.stat().st_mtime
                    if first_mtime is None or mtime < first_mtime:
                        first_mtime = mtime
                    if latest_mtime is None or mtime > latest_mtime:
                        latest_mtime = mtime
                    completed_trials.append({
                        "name": td.name,
                        "target": tdata.get("target"),
                        "endpoint": tdata.get("endpoint"),
                        "status": status,
                        "warmup_seconds": tdata.get("warmup_seconds"),
                        "mtime": mtime,
                    })
                except Exception:
                    pass

        curr_trial = all_entries[-1].name if len(all_entries) > len(completed_trials) else None

        if curr_trial:
            progress_file = trials_dir / curr_trial / "progress.json"
            if progress_file.exists():
                res["progress"] = json.loads(progress_file.read_text())

        if curr_trial:
            last_file = trials_dir / curr_trial / "last-step.json"
            if last_file.exists():
                res["last_step"] = json.loads(last_file.read_text())

        res["trials"] = {
            "total_directories": len(all_entries),
            "completed_count": len(completed_trials),
            "status_counts": status_counts,
            "latest_completed": completed_trials[-1]["name"] if completed_trials else None,
            "current_trial": curr_trial,
            "first_trial_mtime": first_mtime,
            "latest_trial_mtime": latest_mtime,
            "recent_completed": completed_trials[-5:] if completed_trials else [],
        }

    return res


def collect_gce(instance: str, zone: str, project: str) -> Dict[str, Any]:
    b64_code = base64.b64encode(REMOTE_INSPECTOR_CODE.encode("utf-8")).decode("ascii")
    remote_cmd = f"python3 -c \"import base64; exec(base64.b64decode('{b64_code}').decode('utf-8'))\""

    gcloud_bin = shutil.which("gcloud.cmd") or shutil.which("gcloud") or "gcloud"
    cmd = [
        gcloud_bin, "compute", "ssh", instance,
        f"--zone={zone}",
        f"--project={project}",
        "--tunnel-through-iap",
        f"--command={remote_cmd}"
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            shell=(os.name == "nt")
        )
    except subprocess.TimeoutExpired:
        return {"mode": "gce", "error": "Connection timed out connecting to GCE instance"}
    except Exception as e:
        return {"mode": "gce", "error": f"Failed to execute gcloud ssh: {e}"}

    if proc.returncode != 0:
        return {
            "mode": "gce",
            "error": f"SSH command failed (code {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        }

    stdout = proc.stdout
    json_start = stdout.find("{")
    json_end = stdout.rfind("}")
    if json_start == -1 or json_end == -1:
        return {
            "mode": "gce",
            "error": f"Invalid output from remote inspector: {stdout.strip()}"
        }

    try:
        data = json.loads(stdout[json_start : json_end + 1])
        data["mode"] = "gce"
        data["instance"] = instance
        data["zone"] = zone
        data["project"] = project
        return data
    except Exception as e:
        return {
            "mode": "gce",
            "error": f"Failed to parse remote JSON: {e}",
            "raw_output": stdout
        }


def format_duration(seconds: float) -> str:
    if seconds < 0:
        return "0s"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def render_report(data: Dict[str, Any]) -> str:
    lines: List[str] = []
    if "error" in data:
        lines.append(f"Error inspecting benchmark status ({data.get('mode', 'unknown')}):")
        lines.append(f"   {data['error']}")
        return "\n".join(lines)

    mode = data.get("mode", "unknown").upper()
    runner_active = data.get("runner_active", False)
    active_containers = data.get("active_containers", [])
    trials_info = data.get("trials", {})
    plan_info = data.get("plan", {})

    total_sched = plan_info.get("total_scheduled", 0)
    completed_count = trials_info.get("completed_count", 0)
    status_counts = trials_info.get("status_counts", {})
    current_trial = trials_info.get("current_trial")

    if runner_active:
        state_str = "RUNNING (Active)"
    elif completed_count > 0 and (total_sched == 0 or completed_count >= total_sched):
        state_str = "COMPLETED"
    elif completed_count > 0:
        state_str = "STOPPED / INTERRUPTED"
    else:
        state_str = "IDLE / NOT STARTED"

    lines.append(f"## Benchmark Execution Status [{state_str}]")
    lines.append("")
    if mode == "GCE":
        lines.append(f"- **Environment**: GCE VM `{data.get('instance')}` ({data.get('zone')})")
        lines.append(f"- **Project**: `{data.get('project')}`")
    else:
        lines.append("- **Environment**: Localhost")
    lines.append(f"- **Results Directory**: `{data.get('results_dir', 'N/A')}`")

    # Progress bar calculation
    if total_sched > 0:
        pct = (completed_count / total_sched) * 100.0
        bar_len = 24
        filled = int(bar_len * (completed_count / total_sched))
        bar = "=" * filled + (">" if filled < bar_len else "") + " " * (bar_len - filled - (1 if filled < bar_len else 0))
        lines.append(f"- **Progress**: **{completed_count} / {total_sched}** trials ({pct:.1f}%) `[{bar}]`")
    else:
        lines.append(f"- **Completed Trials**: **{completed_count}**")

    # Outcome breakdown
    if status_counts:
        outcomes = ", ".join(f"`{k}`: {v}" for k, v in sorted(status_counts.items()))
        lines.append(f"- **Outcomes**: {outcomes}")

    # Active execution details
    if runner_active or active_containers or current_trial or data.get("tester", {}).get("k6_cmd"):
        lines.append("")
        lines.append("### Active Workload")
        if current_trial:
            lines.append(f"- **Current Trial**: `{current_trial}`")
        if active_containers:
            for c in active_containers:
                lines.append(f"- **Active Container**: `{c['name']}` ({c['image']}) [{c['status']}]")
        if data.get("progress"):
            progress = data["progress"]
            lines.append(f"- **Phase**: `{progress.get('phase')}`; repetition {progress.get('repetition')}; rate {progress.get('rate', '—')} RPS; step {progress.get('step', '—')}")
        if data.get("last_step"):
            m = data["last_step"].get("measurement", {})
            lines.append(f"- **Last Step**: tester CPU {m.get('tester_cpu_pct', '—')}%; dropped {m.get('iterations_dropped', '—')}; saturated {m.get('client_saturated', '—')}")
        if data.get("tester"):
            tester = data["tester"]
            lines.append(f"- **Tester k6**: `{tester.get('k6_cmd') or 'idle'}`")
        if data.get("driver_cmd"):
            driver_brief = data["driver_cmd"].split("/")[-1]
            lines.append(f"- **Workload Driver**: `{driver_brief}`")

    # Timing and ETA
    first_mtime = trials_info.get("first_trial_mtime")
    latest_mtime = trials_info.get("latest_trial_mtime")

    if completed_count > 1 and first_mtime and latest_mtime and latest_mtime > first_mtime:
        elapsed = latest_mtime - first_mtime
        avg_trial_sec = elapsed / max(1, completed_count - 1)
        remaining_trials = max(0, total_sched - completed_count)
        eta_seconds = avg_trial_sec * remaining_trials

        lines.append("")
        lines.append("### Timing & Estimates")
        lines.append(f"- **Elapsed (Trials 1..{completed_count})**: {format_duration(elapsed)}")
        lines.append(f"- **Average Time per Trial**: {format_duration(avg_trial_sec)}")
        if runner_active and remaining_trials > 0:
            now_dt = datetime.datetime.now()
            eta_dt = now_dt + datetime.timedelta(seconds=eta_seconds)
            lines.append(f"- **Estimated Remaining Time**: ~{format_duration(eta_seconds)} ({remaining_trials} trials left)")
            lines.append(f"- **Estimated Completion**: ~{eta_dt.strftime('%H:%M:%S')}")
        elif not runner_active and remaining_trials > 0:
            lines.append(f"- **Remaining Trials**: {remaining_trials} (Runner not actively executing)")

    # Recent completed trials
    recent = trials_info.get("recent_completed", [])
    if recent:
        lines.append("")
        lines.append("### Recent Completed Trials")
        lines.append("| Trial | Target | Endpoint | Status | Warmup |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for r in recent:
            val = r.get("warmup_seconds")
            w_sec = f"{val:.1f}s" if isinstance(val, (int, float)) else (f"{val}s" if val is not None else "-")
            lines.append(f"| `{r.get('name')}` | `{r.get('target')}` | `{r.get('endpoint')}` | `{r.get('status')}` | {w_sec} |")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check benchmark execution status, progress, and ETA.")
    parser.add_argument("--mode", choices=["auto", "gce", "local"], default="auto",
                        help="Check mode: auto (default), gce, or local.")
    parser.add_argument("--instance", help="GCE instance name (default: auto-detected or bench-runner-c3).")
    parser.add_argument("--loadgen", help="GCE tester VM name.")
    parser.add_argument("--zone", help="GCE zone (default: auto-detected or asia-northeast1-b).")
    parser.add_argument("--project", help="GCP project ID (default: auto-detected or sandbox-svc-dev-8rra).")
    parser.add_argument("--results-dir", help="Local or specific benchmark results directory.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of markdown.")
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="Refresh every N seconds.")

    args = parser.parse_args()
    repo_root = find_repo_root()
    tf_defaults = read_terraform_defaults(repo_root)

    instance = args.instance or tf_defaults["instance"]
    zone = args.zone or tf_defaults["zone"]
    project = args.project or tf_defaults["project"]
    loadgen = args.loadgen or tf_defaults["loadgen"]

    mode = args.mode
    if mode == "auto":
        mode = "gce"

    def run_once() -> int:
        if mode == "gce":
            if not project:
                data = {"mode": "gce", "error": "Specify --project or provide Terraform outputs"}
            else:
                data = collect_gce(instance, zone, project)
                tester = collect_gce(loadgen, zone, project)
                if "error" not in tester:
                    data["tester"] = tester
        else:
            data = collect_local(args.results_dir)

        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(render_report(data))
        return 0 if "error" not in data else 1

    if args.watch:
        try:
            while True:
                os.system("cls" if os.name == "nt" else "clear")
                run_once()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nStopped watch.")
            return 0
    else:
        return run_once()


if __name__ == "__main__":
    sys.exit(main())

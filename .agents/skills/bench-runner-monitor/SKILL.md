---
name: bench-runner-monitor
description: Inspect real-time benchmark execution status, trial completion progress, active workloads, and ETA on GCE runner VM or localhost.
---

# Benchmark Runner Monitor

Use this skill when checking whether a benchmark experiment is still running, tracking its trial completion progress, verifying active Docker containers/processes, or estimating time until completion (ETA).

## Quick Check

To inspect the current status on the remote GCE runner (`bench-runner-c3`):

```bash
python .agents/skills/bench-runner-monitor/scripts/check_status.py
```

For continuous monitoring in a terminal (refresh every 10 seconds):

```bash
python .agents/skills/bench-runner-monitor/scripts/check_status.py --watch 10
```

To output raw machine-readable JSON:

```bash
python .agents/skills/bench-runner-monitor/scripts/check_status.py --json
```

To inspect local benchmark results instead of GCE:

```bash
python .agents/skills/bench-runner-monitor/scripts/check_status.py --mode local
```

## What the Monitor Reports

1. **State & Environment**: Identifies whether the runner process (`scripts/bench/run.py`) is `RUNNING`, `COMPLETED`, `STOPPED`, or `IDLE`, along with GCE VM instance details and active results directory.
2. **Progress & Schedule**: Reads `plan.json` and completed `trials/*/trial.json` to calculate progress percentage, completed vs total scheduled trials, and a visual progress bar.
3. **Active Workload**: Identifies the currently running trial index and target, active Docker container (`rails-bench-*`), and the running load driver process (`scripts/bench/driver.py`).
4. **Timing & ETA**: Computes elapsed time, rolling average duration per trial, remaining trial count, and estimated clock time of completion.
5. **Outcome Quality**: Aggregates trial statuses (`passed`, `failed`) and lists the most recent trial completions including warmup convergence times.

See [rationale and design](references/why-and-what.md).

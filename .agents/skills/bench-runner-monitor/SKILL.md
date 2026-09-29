---
name: bench-runner-monitor
description: Inspect local or GCE two-VM Rails/Roundhouse/Spinel benchmark progress, active app and tester processes, capacity search step, trial outcome and telemetry.
---

# Benchmark Runner Monitor

Run `python .agents/skills/bench-runner-monitor/scripts/check_status.py --project PROJECT_ID` to inspect app and tester VMs through IAP. Instance names and zone come from Terraform state if available; otherwise supply `--instance bench-app-c3 --loadgen bench-loadgen-c3 --zone ZONE`. For local results run `--mode local --results-dir PATH`. Use `--json` for machine-readable output and `--watch 10` for refresh.

Read the app's current target, repetition, warmup window or capacity search rate/step from `progress.json`. Check the tester's k6 process separately and inspect per-step tester telemetry in the result directory. Treat completed trial count as progress only; a capacity trial contains variable search steps, so do not infer an ETA from trial count for capacity runs. Compare actual `capacity-search.json`, `warmup.json` and `trial.json` when checking a failed run.

### Implementation Notes:
- **Windows Command Length Limits**: `check_status.py` uses `gzip` compression + `base64` encoding for remote inspector payload scripts over `gcloud compute ssh` to avoid the Windows `cmd.exe` 8,191-character command line limit.
- **Robust Progress Recovery**: `check_status.py` parses `trials/per-run.json` to accurately reflect completed trials and their status even if individual trials raise exceptions before generating `trial.json`.

See [rationale and design](references/why-and-what.md).


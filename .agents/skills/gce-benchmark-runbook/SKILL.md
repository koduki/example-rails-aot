---
name: gce-benchmark-runbook
description: Prepare or execute two-VM C3 Rails/Roundhouse comparisons, targeted follow-up experiments and Spinel connection diagnostics; recover raw evidence, publish the report and stop both VMs. Use for GCE benchmark execution, retest instructions or formal result publication, separate from quick GitHub Actions checks.
---

# GCE benchmark runbook

Use the repository's `bench/profiles/gce-c3-capacity.yml` and [formal experiment contract](../../../docs/gce-c3-benchmark-report.md). Read [report guidance](references/report-writing.md) before interpreting results. Do not substitute the hosted Actions `quick` or manual `full` results for GCE capacity.

## Select the experiment before launching

For follow-up to the October 1 release, read [the current report](../../../docs/roundhouse-rails-jit-aot-report.md) and [targeted retest instructions](../../../docs/gce-c3-followup-instructions.md). Use `scripts/bench/retest_plan.py`, not the default 9-target/120-hour profile. Plan generation is allowed when asked for instructions; that request does not authorize starting VMs or load.

Prepare `--experiment matched-rate --output NEW_PATH` without `--execute`. Review all generated profiles and argv. Execution requires a distinct new output path, `--execute`, matching `--preflight-file` and all four remote placement flags. Rebuild and rerun preflight at the immutable merged source SHA; an earlier release's manifest is not reusable.

Order: common 10 RPS for the main six targets (3 repetitions), then separate CRuby 4-target and JRuby JIT 2-target capacity cohorts (5 repetitions each), then Spinel pool 10/512 at 10/15/25 RPS (3 each). JRuby Off remains supplementary. Review/recover each experiment before the next. Keep trace cohorts separate and conditional. See instructions for 6/8/6/4-hour global deadlines; budget exhaustion must preserve partial results and not-run rows. Spinel's deadline covers all six cells.

Fixed-rate `measurement_slo_required` enforces the same 100-ms/error/client/App criteria as capacity; successful 10 RPS does not estimate capacity. Recovery health uses its own recorded latency threshold and retains performance SLO separately. Healthy responses and TCP queues never prove HTTP queue drainage. Inspect phase wall time, actual k6 invocation, optional socket evidence and actual JFR capture; missing evidence stays unknown. Baselines disable diagnostics/socket/JFR. Retain both-VM cleanup and source/final manifests under the same root run. The single-profile `run_gce_suite.py` does not implement this multi-experiment workflow.

## Original full-matrix lifecycle

1. Record the exact remote commit SHA, project, zone, existing VPC/subnet and Cloud NAT. Create a local `infra/terraform/terraform.tfvars` from `example.tfvars.example`. Review `terraform plan`; the module must create two private `c3-standard-4` VMs (`bench-app-c3`, `bench-loadgen-c3`), not a new VPC or NAT. (If migrating from legacy state with `bench-runner-c3`, verify its replacement). Apply only with operator authorization. Obtain `app_internal_ip`, `app_instance_name`, `loadgen_instance_name` and `zone` from Terraform outputs. Confirm both VMs have `/var/run/startup-completed`.
   - **SSH & Permissions**: On Windows with PuTTY/Plink, pass `echo y | ...` on first connection to cache host keys. In PowerShell, use single quotes for remote commands to prevent local `$()` expansion.
   - **Docker Group**: Add the SSH user to the docker group on the app VM (`sudo usermod -aG docker "$USER"`), then start a new SSH session to confirm `docker ps` works without sudo.
   - **Internal SSH**: Verify the app VM can SSH to the loadgen VM over the private network: `gcloud compute ssh bench-loadgen-c3 --internal-ip --zone ZONE --project PROJECT_ID --command "k6 version"`.
2. Start **both** VMs if stopped. On the app VM, sync and check out the recorded remote commit in a clean clone. Build all targets passing the remote flags so CPU allocation recognizes the remote load generator:
   ```bash
   python3 scripts/bench/run.py build --profile bench/profiles/gce-c3-capacity.yml \
     --remote-loadgen bench-loadgen-c3 --target-host APP_INTERNAL_IP \
     --gce-project PROJECT_ID --gce-zone ZONE \
     --output bench-results/build-RUN_ID
   ```
   *(Note: Omitting `--remote-loadgen` causes `run.py` to fail with `ValueError: Need distinct allowed CPUs` because 4 vCPUs cannot be split between a 4-vCPU app and a local load generator).* Keep the build logs and image IDs. Never build from a moving branch name without recording its resulting SHA.
3. On the app VM, use the actual private app IP, tester instance name, project and zone in all following commands. Run the strict preflight, then launch the formal run:

   ```bash
   python3 scripts/bench/run.py preflight --profile bench/profiles/gce-c3-capacity.yml \
     --remote-loadgen bench-loadgen-c3 --target-host APP_INTERNAL_IP \
     --gce-project PROJECT_ID --gce-zone ZONE \
     --output bench-results/preflight-RUN_ID
   ```

   The preflight manifest next to `preflight.json` binds the code, image IDs and target coverage; rerun preflight after any change. Do not run the quick or hosted `full` profile as a replacement.

   The formal capacity run executes 45 trials (9 targets × 5 repetitions) with warmup, search, and confirmation. Elapsed time depends on convergence, confirmation refinement and recovery attempts; do not promise a fixed duration. Check the total budget and retain any not-run trials. Read [the measurement protocol](references/measurement-protocol.md) before selecting warmup VUs, bounds and tolerance. To prevent SSH connection drops from aborting the run, launch it in a detached `tmux` session on `bench-app-c3`:
   ```bash
   tmux new-session -d -s bench 'python3 scripts/bench/run.py run \
     --profile bench/profiles/gce-c3-capacity.yml \
     --remote-loadgen bench-loadgen-c3 --target-host APP_INTERNAL_IP \
     --gce-project PROJECT_ID --gce-zone ZONE \
     --preflight-file bench-results/preflight-RUN_ID/preflight.json \
     --output bench-results/gce-RUN_ID > bench-results/gce-run.log 2>&1'
   ```

   Monitor live progress non-invasively from the operator machine with [bench-runner-monitor](../bench-runner-monitor/SKILL.md) (`python .agents/skills/bench-runner-monitor/scripts/check_status.py --project PROJECT_ID`) and retain failed trials instead of silently retrying them.
4. Generate or regenerate the machine report on the app VM with `python3 scripts/bench/run.py report --output bench-results/gce-RUN_ID`. Download the **whole** `gce-RUN_ID` directory and the preflight/build logs through IAP (`gcloud compute scp --recurse --tunnel-through-iap`), and verify the local copy before stopping or deleting disks. The report paths are `gce-summary.json` and `gce-summary.md`.
   - **Encoding**: On Windows operator machines, python scripts writing or reading reports must specify `encoding='utf-8'` to avoid `cp932` `UnicodeEncodeError` on mathematical symbols such as `≤` (`\u2264`).
   - Inspect every repetition and its raw k6 and telemetry artifacts using the [report guidance](references/report-writing.md). Add a reviewed interpretation to `docs/gce-c3-benchmark-report.md` only after the evidence is complete; distinguish incomplete data and limitations explicitly.
5. Always stop **both** VMs after local recovery, including failure and interruption. From the operator machine run:
   ```bash
   python scripts/bench/gce_cleanup.py --project PROJECT_ID --zone ZONE --status-file bench-results/gce-RUN_ID/cleanup.json
   ```
   Verify both instances report `TERMINATED`.
   - **Windows execution**: `scripts/bench/gce_cleanup.py` must use `shutil.which('gcloud')` to resolve `gcloud.cmd` on Windows shells to prevent `[WinError 2]`.
   - For an end-to-end command, wrap it with `gce_cleanup.py ... --after COMMAND ...` so the stop is attempted in `finally` after download and report review. If the controlling process dies, inspect VM states manually and retry the stop. Stopped SSDs continue to incur charges. Once artifacts are secure and the environment will not be reused, review `terraform destroy` for the same state/tfvars and remove only resources owned by the module; retain the existing VPC/NAT.

### Automated Unmanned Orchestration & Disk Lifecycle Wrapper
To execute the full lifecycle in a single command from the operator machine:
```bash
python scripts/bench/run_gce_suite.py --project PROJECT_ID --zone ZONE --run-id RUN_ID --source-ref SOURCE_SHA
```
This orchestrator automatically:
1. Verifies/starts both VMs (`bench-app-c3`, `bench-loadgen-c3`) and discovers private IPs
2. Runs container build and strict preflight validation
3. Executes the full benchmark run and generates reports
4. Downloads all raw artifacts via SCP through IAP
5. Verifies any received `SHA256SUMS` before regeneration, records exact fetched source SHA in `execution.json`, and includes local cleanup evidence in the final manifest
6. Attempts both stops in `finally`, records timestamped results in `cleanup.json`, and returns failure unless both report `TERMINATED`
7. Displays persistent disk cost warnings and provides safe `terraform destroy` guidance (or `--auto-destroy` if authorized).

If a separate `gce-benchmark-runner` automation script becomes available, inspect its current implementation before using it: require the two VM names, exact source SHA, complete local artifact recovery, independent stop/verification of **both** VMs, and an explicit disk lifecycle. Its earlier one-VM defaults must not override this runbook.

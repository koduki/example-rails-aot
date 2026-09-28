---
name: gce-benchmark-runbook
description: Execute the formal two-VM c3-standard-4 Rails/Roundhouse/Spinel capacity benchmark, recover raw artifacts, write the evidence-based GCE report, and clean up both VMs and disks. Use for GCE benchmark execution or formal result publication, separate from quick GitHub Actions checks.
---

# GCE benchmark runbook

Use the repository's `bench/profiles/gce-c3-capacity.yml` and [formal experiment contract](../../../docs/gce-c3-benchmark-report.md). Read [report guidance](references/report-writing.md) before interpreting results. Do not substitute the hosted Actions `quick` or manual `full` results for GCE capacity.

1. Record the exact remote commit SHA, project, zone, existing VPC/subnet and Cloud NAT. Review `terraform plan` with a local tfvars file; the module must create two private `c3-standard-4` VMs (`bench-app-c3`, `bench-loadgen-c3`), not a new VPC or NAT. Apply only with the operator's authorization and configured GCP access. Obtain `app_internal_ip`, `app_instance_name`, `loadgen_instance_name` and `zone` from Terraform outputs. Confirm both VMs have their `/var/run/startup-completed` marker, Docker works as the app's SSH user, and k6 runs on the tester.
2. Start **both** VMs if stopped. On the app VM, sync and check out the recorded remote commit in a clean clone, then build all nine targets with `python3 scripts/bench/run.py build --profile bench/profiles/gce-c3-capacity.yml --output bench-results/build-RUN_ID`. Keep the build logs and image IDs. Never build from a moving branch name without recording its resulting SHA.
3. On the app VM, use the actual private app IP, tester instance name, project and zone in all following commands. Run the strict preflight, then the formal run:

   ```bash
   python3 scripts/bench/run.py preflight --profile bench/profiles/gce-c3-capacity.yml \
     --remote-loadgen bench-loadgen-c3 --target-host APP_INTERNAL_IP \
     --gce-project PROJECT_ID --gce-zone ZONE \
     --output bench-results/preflight-RUN_ID
   python3 scripts/bench/run.py run --profile bench/profiles/gce-c3-capacity.yml \
     --remote-loadgen bench-loadgen-c3 --target-host APP_INTERNAL_IP \
     --gce-project PROJECT_ID --gce-zone ZONE \
     --preflight-file bench-results/preflight-RUN_ID/preflight.json \
     --output bench-results/gce-RUN_ID
   ```

   The preflight manifest next to `preflight.json` binds the code, image IDs and target coverage; rerun preflight after any change. Do not run the quick or hosted `full` profile as a replacement. Monitor with [bench-runner-monitor](../bench-runner-monitor/SKILL.md) and retain failed trials instead of silently retrying them.
4. Generate or regenerate the machine report on the app VM with `python3 scripts/bench/run.py report --output bench-results/gce-RUN_ID`. Download the **whole** `gce-RUN_ID` directory and the preflight/build logs through IAP, and verify the local copy before stopping or deleting disks. The report paths are `gce-summary.json` and `gce-summary.md`. Inspect every repetition and its raw k6 and telemetry artifacts using the [report guidance](references/report-writing.md). Add a reviewed interpretation to `docs/gce-c3-benchmark-report.md` only after the evidence is complete; distinguish incomplete data and limitations explicitly.
5. Always stop **both** VMs after local recovery, including failure and interruption. From the operator machine run `python3 scripts/bench/gce_cleanup.py --project PROJECT_ID --zone ZONE --status-file bench-results/gce-RUN_ID/cleanup.json`; verify both are `TERMINATED`. For an end-to-end command, wrap it with `gce_cleanup.py ... --after COMMAND ...` so the stop is attempted in `finally` after download and report review. If the controlling process dies, inspect VM states manually and retry the stop. Stopped SSDs continue to incur charges. Once artifacts are secure and the environment will not be reused, review `terraform destroy` for the same state/tfvars and remove only resources owned by the module; retain the existing VPC/NAT.

If a separate `gce-benchmark-runner` automation script becomes available, inspect its current implementation before using it: require the two VM names, exact source SHA, complete local artifact recovery, independent stop/verification of **both** VMs, and an explicit disk lifecycle. Its earlier one-VM defaults must not override this runbook.

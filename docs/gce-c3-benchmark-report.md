# GCE c3-standard-4 × 2 Benchmark Report

## Executive Summary

Formal measurements are pending. This document contains the preregistered experiment and report contract. `scripts/bench/gce_report.py` creates `gce-summary.md` and `gce-summary.json` from raw artifacts after a completed GCE run; transfer the results here only after inspecting all repetitions and correctness gates. No hosted Actions throughput is a GCE capacity result.

## Experiment

The question is maximum sustainable application throughput with one c3-standard-4 app VM, compared across nine Rails, Roundhouse Ruby and Roundhouse + Spinel configurations. A second c3-standard-4 VM generates k6 load over the existing private VPC. Both instances have no external IP and use existing Cloud NAT for outbound setup. The alternate primary workload is **`app-sliced-page20-1000`**: `GET /articles?page=1` with 1000 articles, ordered in the app and 20 results returned per page. Roundhouse + Spinel currently fails on ActiveRecord `offset` (`NoMethodError: undefined method 'offset' for unknown`, AOT smoke in PR #46). All runtimes therefore read the ordered 1000-row relation and select the same 20 in application code. This is real HTTP pagination, but it is not database LIMIT/OFFSET pagination; treat that as a recorded blocker for a future DB-paged scenario.

App CPU and memory summaries are collected around the 120-second confirmation, including remote orchestration overhead; per-step app telemetry is retained separately. An unavailable app sample, positive CPU throttling delta, tester network error or tester saturation invalidates a repetition.

Each target runs on the same app VM, same commit, same fixture construction and 4 vCPU allocation. Five rotations reverse target order across repetitions. Warmup uses closed-loop 32 VUs for 180–900 seconds, then each repetition searches a bounded open-arrival capacity bracket. The chosen rate is confirmed for 120 seconds. SLO: p99 ≤ 100 ms, errors < 0.1%, no dropped iterations or tester saturation. A failed confirmation is not a capacity result. See `bench/profiles/gce-c3-capacity.yml` for exact values.

## Result contract

The generated report provides correctness eligibility; every repetition's capacity, latency, errors and warmup; worst p99 and error rate; app memory and CPU telemetry; paired same-repetition Roundhouse and JIT ratios; JRuby tier variation; and raw artifact paths. Incomplete targets have no aggregate capacity. Capacity search steps and tester CPU/network/memory logs remain attached to each trial.

Spinel includes its native HTTP runtime, DB adapter, scheduling and memory management as well as AOT compilation. Its observed difference is a result for the Roundhouse + Spinel architecture, not an isolated AOT compiler speedup. CRUD uses a different workload and SQLite's single-writer constraint remains even with distinct article IDs.

## Execution

Use the repo [GCE benchmark runbook](../.agents/skills/gce-benchmark-runbook/SKILL.md) for execution, report review and cost cleanup. PR/main Actions run quick correctness checks by default. Dispatch `Benchmark Pipeline` and `Rails to Spinel AOT Pipeline` with mode `full` for their longer hosted checks; neither is the formal two-VM capacity run.

1. Set `project_id`, `network_name`, and `subnetwork_name` in a local tfvars file (see `infra/terraform/example.tfvars.example`). Verify that the subnet and existing Cloud NAT cover the selected zone/region. Run `terraform init`, `terraform validate`, `terraform plan`, and apply when ready to provision the two VMs.
2. Wait for both `/var/run/startup-completed` markers. Verify Docker only on app and k6 only on tester. On app, clone the exact commit, build all nine targets, and supply the app's actual internal IP, project and zone as CLI arguments. Keep the tracked environment template unchanged.
3. On app, run `python3 scripts/bench/run.py build --targets rails-cruby-off,rails-cruby-yjit,rails-jruby-off,rails-jruby,emit-cruby-off,emit-cruby-yjit,emit-jruby-off,emit-jruby,spinel --output bench-results/build`; run `python3 scripts/bench/run.py run --env-file bench/environments/gce-c3-standard-4.env --target-host APP_INTERNAL_IP --gce-project PROJECT_ID --gce-zone ZONE --output bench-results/gce-c3-capacity-RUN_ID`. Preserve all raw artifacts. A strict preflight precedes measurement.
4. Review `gce-summary.md` and `gce-summary.json`, failed or incomplete repetitions, source SHA and environment. Do not publish performance conclusions if remote execution, pagination, tester headroom or correctness fails.

## Cost cleanup and artifact retention

Download the complete raw result directory to the operator machine, including `plan.json`, `env.json`, `preflight/`, `trials/`, `gce-summary.json` and `gce-summary.md`. Verify the local copy before removing any VM or disk. The report is generated on the app VM; stopping it before the copy completes may strand the only copy of failed or incomplete runs.

Stop **both** app and tester VMs at the end of the workflow, including preflight/build errors, failed measurements, download failures and operator interruption. From the operator machine, run:

```bash
python3 scripts/bench/gce_cleanup.py --project PROJECT_ID --zone ZONE \
  --status-file bench-results/RUN_ID/cleanup.json
```

The helper initiates both stops independently, waits until each reports `TERMINATED`, records their final status, and fails if either cannot be confirmed. It is idempotent for already stopped VMs. With an external end-to-end runner, invoke it as `gce_cleanup.py --project PROJECT_ID --zone ZONE --status-file LOCAL_PATH --after COMMAND ...`: it runs the **entire** workflow (including artifact download and report verification) and stops both VMs in `finally`, even when the command fails. A `--no-auto-stop` option in that external runner must never be the default for unattended formal runs. If the wrapper itself is killed or the operator machine loses power, manually check both VM states in GCE and run the stop command again. Do not treat a stop attempt alone as confirmation.

Stopped VMs retain their SSD boot disks and any other provisioned resources, so their costs do not become zero. When the local artifacts and report have been checked and the environment will not be reused, run `terraform destroy` against the same state/tfvars after reviewing its plan. This Terraform module references the existing VPC/subnet and creates no Cloud NAT; destroying this module must not delete the shared network or NAT. Preserve the local raw artifacts independently of the Terraform state. Record whether the cleanup ended in verified stop, deliberate retention, or a failure needing manual action.

GCE execution and actual measurements have not been run from this workspace. The generated report is the source for results once the infrastructure and GCP access are available.

# GCE c3-standard-4 × 2 Benchmark Report

## Executive Summary

Formal measurements are pending. This document contains the preregistered experiment and report contract. `scripts/bench/gce_report.py` creates `gce-summary.md` and `gce-summary.json` from raw artifacts after a completed GCE run; transfer the results here only after inspecting all repetitions and correctness gates. No hosted Actions throughput is a GCE capacity result.

## Experiment

The question is maximum sustainable application throughput with one c3-standard-4 app VM, compared across nine Rails, Roundhouse Ruby and Roundhouse + Spinel configurations. A second c3-standard-4 VM generates k6 load over the existing private VPC. Both instances have no external IP and use existing Cloud NAT for outbound setup. The primary endpoint is `GET /articles?page=1` with 1000 articles and 20 results per page.

Each target runs on the same app VM, same commit, same fixture construction and 4 vCPU allocation. Five rotations reverse target order across repetitions. Warmup uses closed-loop 32 VUs for 180–900 seconds, then each repetition searches a bounded open-arrival capacity bracket. The chosen rate is confirmed for 120 seconds. SLO: p99 ≤ 100 ms, errors < 0.1%, no dropped iterations or tester saturation. A failed confirmation is not a capacity result. See `bench/profiles/gce-c3-capacity.yml` for exact values.

## Result contract

The generated report provides correctness eligibility; every repetition's capacity, latency, errors and warmup; worst p99 and error rate; app memory and CPU telemetry; paired same-repetition Roundhouse and JIT ratios; JRuby tier variation; and raw artifact paths. Incomplete targets have no aggregate capacity. Capacity search steps and tester CPU/network/memory logs remain attached to each trial.

Spinel includes its native HTTP runtime, DB adapter, scheduling and memory management as well as AOT compilation. Its observed difference is a result for the Roundhouse + Spinel architecture, not an isolated AOT compiler speedup. CRUD uses a different workload and SQLite's single-writer constraint remains even with distinct article IDs.

## Execution

1. Set `project_id`, `network_name`, and `subnetwork_name` in a local tfvars file (see `infra/terraform/example.tfvars.example`). Verify that the subnet and existing Cloud NAT cover the selected zone/region. Run `terraform init`, `terraform validate`, `terraform plan`, and apply when ready to provision the two VMs.
2. Wait for both `/var/run/startup-completed` markers. Verify Docker only on app and k6 only on tester. On app, clone the exact commit, build all nine targets, and update `bench/environments/gce-c3-standard-4.env` with the app's actual internal IP, project and zone.
3. On app, run `python3 scripts/bench/run.py build --targets rails-cruby-off,rails-cruby-yjit,rails-jruby-off,rails-jruby,emit-cruby-off,emit-cruby-yjit,emit-jruby-off,emit-jruby,spinel --output bench-results/build`; run `python3 scripts/bench/run.py run --env-file bench/environments/gce-c3-standard-4.env --output bench-results/gce-c3-capacity-RUN_ID`. Preserve all raw artifacts. A strict preflight precedes measurement.
4. Review `gce-summary.md` and `gce-summary.json`, failed or incomplete repetitions, source SHA and environment. Do not publish performance conclusions if remote execution, pagination, tester headroom or correctness fails.

GCE execution and actual measurements have not been run from this workspace. The generated report is the source for results once the infrastructure and GCP access are available.

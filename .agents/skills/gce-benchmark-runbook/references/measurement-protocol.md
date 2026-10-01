# C3 capacity and failure measurement protocol

Use when evaluating an existing release or planning a new experiment. This protocol defines evidence and commands; it does not itself authorize a cloud run or resource creation.

## Identity and correctness

Record release/archive hash, measured source SHA, report revision, profile hash, image IDs and generated-code identity. Keep the release tag unchanged. Validate all archive paths and entries, then verify the published manifest before analysis. Record workload, fixture/response row counts, endpoint, app/tester placement, CPU topology (4 vCPUs are not necessarily 4 physical cores), runtime/JIT flags and DB adapter versions. Page eligibility is independent of complete CRUD correctness; retain excluded CSRF/invalid-input cases.

## Capacity interval

The formal C3 profiles use 1-RPS minimum, 25-RPS initial probe, 5-RPS absolute and 5% relative tolerance (the smaller applies), a bounded probe count, five repetitions and 120-second confirmations. Check the current profile rather than assuming these values. Short exploration identifies a candidate. A failed sustained candidate tightens the upper bound; halving finds a lower point, then sustained midpoint tests refine upward. k6 uses an integer iteration rate per `timeUnit`; the read script represents fractional probes as rate × 1,000 per 1,000 seconds ([official executor contract](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/)). The report retains lower offered rate, achieved successful RPS, upper failed rate/duration, width and tolerance. Short and long upper failures have different evidentiary strength. No observed upper bound means no estimated maximum; retain the confirmed lower point.

Reject client/network/transport/missing latency evidence without converting it to a server-capacity bound. Direct latency/error/integrity violations establish SLO failure, not a root cause. Dropped iterations alone are incomplete issuance. A repeated boundary reversal is unstable. Preserve all probes, budget failures and final trial state.

After an overload step, the runner executes bounded low-load health probes. `recovery.json` records every attempt, success/failure and `server_queue_drained: null`. No server outstanding-work gauge exists yet. A healthy probe does not certify complete drainage; if it fails, end the trial and restart/rewarm as a separately identified trial. Do not silently retry or splice containers into one repetition.

The revised four-VU warmup differs from the September 30 release's 32-VU warmup. Keep cohorts separate. Error-free statistical convergence is required for formal ranking; merely stable failed throughput is not convergence.

## Diagnostic sweep

On the app host, prepare cells without launching them:

```bash
python3 scripts/bench/load_sweep.py --mode vus --values 1,2,4,8,16,32 \
  --targets rails-jruby-off,emit-jruby-off,emit-jruby,spinel \
  --output bench-results/sweep-vus-RUN_ID
python3 scripts/bench/load_sweep.py --mode rates --values 1,5,10,25,50,100 \
  --targets rails-jruby-off,emit-jruby-off,emit-jruby,spinel \
  --output bench-results/sweep-rates-RUN_ID
```

Each cell uses a fresh container, three repetitions, one-VU warmup, 120-second measurement and complete phase artifacts. A sweep is diagnostic and does not produce a formal capacity estimate. Review `sweep-plan.json`; invalid output paths/values are rejected. To execute, choose a new output directory and add `--execute --preflight-file PATH --remote-loadgen TESTER --target-host PRIVATE_IP --gce-project PROJECT --gce-zone ZONE`. Execution requires fresh matching preflight and the separate tester. The planner creates no VMs and performs no Terraform operation. Stop both VMs through the runbook's cleanup workflow after recovering all artifacts.

Uninstrumented is the default. Use `--instrumented` for a distinct diagnostic cohort; record actual flags and trace overhead. An ordered low/high/low list still uses separate containers and cannot establish in-container recovery. That experiment requires one container, request timestamps and outstanding-work/queue gauges; it remains pending.

## Stage and trace bundle

Collect SQL start/end, materialization/preload/render timings, allocation/GC deltas, HTTP accept/connection/worker waits and per-request identity. JRuby needs JFR or stack sampling plus compiler/tier/GC events; Spinel needs worker/DB/connection timing. Preserve success and timeout requests. Use CPU and wall-time clocks appropriately and align client/server timestamps. No traced stack or stage timing means no confirmed interpreter, DB or scheduling cause. HTTP minimum is not service time; a hypothetical nested-loop count is not a measured microbenchmark.

Fix only the stage isolated by those measurements. Publish microbenchmark code, invocation, runtime/JIT mode, fixture and repeated raw samples, then rerun the full workload with fresh images/preflight. Performance claims must describe the full serving stack where adapters/servers differ.

## Report and verification

List all repetitions and missing trial artifacts; recover metadata from the per-run index without inventing defaults. Pair only the same-repetition eligible sustained candidates. Label partial pair counts and legacy unresolved intervals. Compare resources at equal offered rate, duration and measurement definition; bytes / 2^20 is MiB and container memory is not process RSS. Warmup throughput and fixed-rate RPS do not imply capacity or JIT interaction.

Retain `execution.json`, `env.json`, `plan.json`, preflight/build logs, complete trial directories and `cleanup.json`. Verify received hashes before recomputing them. Both VM stop checks need timestamps and `TERMINATED`; absent records do not establish billing state. Include stop evidence in the final checksum manifest.

Quick validation is unit/CLI contracts, profile/planner dry runs and Terraform fmt/init/validate. Docker builds, nine-target correctness and actual load stay manual. No quick CI test launches GCE resources or a load sweep.

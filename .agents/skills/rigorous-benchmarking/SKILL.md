---
name: rigorous-benchmarking
description: Design reproducible multi-runtime load experiments, check workload integrity and warmup, and report fixed-rate results without inferring unmeasured capacity or JIT speedups.
---

# Rigorous benchmarking

Use for comparing Rails, JRuby, and Spinel runtime configurations. Read [validation report](../../../docs/roundhouse-rails-jit-aot-report.md) and [benchmark scripts](../../../scripts/bench/) first. Run preflight for the selected route before timing. The benchmark-only CRUD profile admits only the successful operations it executes; invalid input and CSRF rejection remain separate correctness failures.

1. Record CPU topology, app and load-generator placement, CPU quotas, runtime/JIT identity, fixture size, and target commit. Leave enough CPU budget for the client.
2. Choose the question and workload. Fixed offered RPS measures latency, errors, and resource use **at that rate**. To compare maximum sustained throughput and derive JIT interaction ratios, add a separate capacity search with valid repeated trials and the same endpoint and placement.
3. For an open-arrival smoke trial, use the [k6 example](resources/k6-open-arrival-template.js), setting `TARGET_URL`, `TARGET_RPS`, `DURATION`, and `ARTICLE_ID` (default 1; fixture must exist). Run warmup separately, then measure repeated steady-state windows.
4. Reject a trial with dropped iterations or unexpected HTTP/body checks. Drops mean scheduled work was not issued; inspect server latency, configured/max VUs, and generator CPU before attributing the cause. Examine latency, errors, CPU, GC/JIT logs, and time trends for warmup; fixed-rate throughput CV alone cannot establish convergence.
5. Report medians and variation over valid repetitions, p50/p95/p99, achieved RPS, and telemetry with exact metric labels. `peak_container_memory_bytes` is container memory usage, not process RSS. State which speedup ratios are supported by a measured capacity search; Spinel comparisons include server and DB adapter differences.

6. For application slicing (1,000 rows loaded before selecting 20), allocations, association work and DB/HTTP waits are competing explanations. Inspect generated serving code and measure stages before identifying a bottleneck. HTTP minimum is not service time; user CPU is not a stack trace. Never substitute a theoretical loop-count model for measured timings.
7. Capacity is an interval: retain the 120-second confirmed lower point, failed upper rate and duration, width and tolerance. After a failed confirmation, halve only to find a lower point, then refine upward; do not stop at the first lower pass. Invalid issuance/client telemetry does not establish a SUT failure bound. For legacy data without intervals, label candidate ratios and expose the uncertainty.
8. Use [the measurement protocol](../gce-benchmark-runbook/references/measurement-protocol.md) for low-load diagnostic sweeps, recovery limits, same-rate resource comparisons and the required trace bundle. Instrumented runs and changes in warmup VUs form separate cohorts.

The template is a standalone example and is not wired into the repository's `scripts/bench` runner. The formal GCE two-VM open-arrival capacity benchmark results and analysis are published in [docs/gce-c3-benchmark-report.md](../../../docs/gce-c3-benchmark-report.md). See [rationale](references/why-and-what.md).


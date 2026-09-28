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

The template is a standalone example and is not wired into the repository's `scripts/bench` runner. The [current report](../../../docs/roundhouse-rails-jit-aot-report.md) records current measurements; GCE open-arrival capacity work remains future work. See [rationale](references/why-and-what.md).

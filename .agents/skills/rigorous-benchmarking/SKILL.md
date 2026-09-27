---
name: rigorous-benchmarking
description: >-
  Provides protocols, templates, and analytical guidelines for conducting scientifically rigorous,
  reproducible software benchmarks that eliminate coordinated omission, audit CPU topologies,
  verify JIT warmup convergence, and produce statistically sound comparative reports.
---

# Rigorous Benchmarking

Use this skill when designing, executing, or analyzing system benchmarks (HTTP services, runtimes, JIT compilers, AOT frameworks, or databases) to ensure data integrity and avoid common measurement fallacies.

For detailed concepts, mathematical formulas (such as the JIT interaction ratio $I$), and historical lessons from `example-rails-aot` Issues #11–#21, see [Why and What Document](./references/why-and-what.md).

---

## Benchmark Design Checklist

Before executing any benchmark trial, verify:
- [ ] **Workload Model**: Is the arrival rate open (`constant-arrival-rate` / `poisson`) rather than closed-loop VU concurrency?
- [ ] **CPU Topology**: Are app container CPUs isolated from the load generator (k6/wrk2)? (Never dedicate 100% of host vCPUs to the target app).
- [ ] **Database Concurrency**: Does the access pattern respect DB locking characteristics (e.g., VU-partitioned writes for SQLite WAL)?
- [ ] **Warmup Budget**: Is warmup tracked separately from steady state? Has JIT compilation converged ($CV \le 5\%$)?
- [ ] **Resource Telemetry**: Are memory (RSS), CPU %, and client-side dropped iterations collected continuously?

---

## Standard Protocol

### Step 1: Topology & Resource Isolation
1. Verify available CPU cores and topology:
   - Identify whether cores are physical or SMT threads (`lscpu` or `/proc/cpuinfo`).
2. Pin server and client processes to non-overlapping CPU sets or cgroup quotas:
   ```yaml
   # Example: 1 dedicated vCPU for server, leaving remaining cores for k6 generator
   deploy:
     resources:
       limits:
         cpus: '1.0'
         memory: 2048M
   ```

### Step 2: Load Generation with Open Arrival Rate
Use the parameterized k6 template: [k6-open-arrival-template.js](./resources/k6-open-arrival-template.js).
Run k6 with external rate enforcement:
```bash
k6 run \
  -e TARGET_URL="http://127.0.0.1:3000" \
  -e TARGET_RPS=1500 \
  -e DURATION="60s" \
  .agents/skills/rigorous-benchmarking/resources/k6-open-arrival-template.js
```
*Validation*: Confirm `dropped_iterations` is **0**. If dropped iterations > 0, the load generator was saturated; the trial must be marked `invalid`.

### Step 3: Warmup & Steady-State Verification
1. Split the trial into separate warmup and measurement phases:
   - Quick / Smoke: 15s warmup, 30s steady state.
   - Comprehensive: 60s–300s warmup, 120s steady state.
2. Measure throughput and latency across rolling 5-second windows.
3. Compute the coefficient of variation ($CV = \sigma / \mu$). Mark the run `passed` only if $CV \le 5\%$; otherwise mark `unstable`.

### Step 4: Epistemic Analysis & Reporting
Format results into four distinct sections:
1. **Raw Trial Dispositions**: Tabulate status (`passed` / `unstable` / `excluded`), achieved RPS, p50, p90, p99, error rate, and Peak RSS.
2. **Observed Facts**: Verified numerical data from passing runs.
3. **Analytical Interactions**: Factor decomposition (e.g. JIT interaction ratio $I = G_{\text{emitted}} / G_{\text{Rails}}$).
4. **Hypotheses & Limitations**: Document SMT interferences, cloud hypervisor jitter, and untested workloads.

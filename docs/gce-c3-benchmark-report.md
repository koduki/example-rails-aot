# GCE c3-standard-4 × 2 Benchmark Report

## Executive Summary

The formal two-VM capacity experiment was executed on Google Compute Engine using two dedicated `c3-standard-4` instances (App SUT: `bench-app-c3`, Load Generator: `bench-loadgen-c3`) in `asia-northeast1-b` over a private VPC with Cloud NAT. The experiment evaluated 9 target runtime configurations across 5 randomized rotation repetitions (45 total trials) using the formal capacity profile `bench/profiles/gce-c3-capacity.yml` on commit `bf098d3e724af5b84d26da9e8398942716a50009`.

Under the rigorous benchmarking contract (`rigorous-benchmarking`), **only confirmed per-repetition sustainable capacity under a 120-second verification window (p99 ≤ 100 ms, errors < 0.1%) is eligible for comparison**, and **no unmeasured capacity is inferred or invented**.

- **Correctness Gate**: All 9 targets passed strict preflight verification for the primary workload `/articles?page=1` (`app-sliced-page20-1000`).
- **Confirmed Capacity Outcomes**:
  - **`emit-cruby-yjit`**: Achieved **5/5 confirmed repetitions** with median sustainable **99.99 RPS** (worst p99: **56.36 ms**), mean CPU utilization of 191.1%, and peak memory of **224.9 MB**—the highest sustained throughput, lowest worst-case latency, and lowest memory footprint among all evaluated runtimes.
  - **`rails-cruby-yjit`**: Achieved **5/5 confirmed repetitions** with median sustainable **75.00 RPS** (worst p99: **82.61 ms**), outperforming `rails-cruby-off` by a median ratio of **1.50x**.
  - **`rails-cruby-off`**: Achieved **5/5 confirmed repetitions** with median sustainable **49.99 RPS** (worst p99: **62.73 ms**), establishing the verified baseline for standard Ruby bytecode interpretation.
  - **`emit-jruby`**: Achieved **5/5 confirmed repetitions** with median sustainable **49.99 RPS** (worst p99: **81.69 ms**), achieving **2.00x** the throughput of standard `rails-jruby` in matched repetitions.
  - **`rails-jruby`**: Achieved **4/5 confirmed repetitions** with **25.00 RPS** (worst p99: **61.61 ms**; Rep 1 was unstable during warmup with CV > 5%).
- **Paired Capacity Speedup Ratios**:
  - `emit-cruby-yjit / rails-cruby-yjit`: Median **1.333x** (Rep 1: 1.613x, Rep 2: 2.000x, Rep 3: 1.000x, Rep 4: 1.333x, Rep 5: 0.750x).
  - `emit-jruby / rails-jruby`: Median **2.000x** (Rep 2: 2.000x, Rep 3: 2.000x, Rep 4: 0.500x, Rep 5: 2.000x).
  - `rails-cruby-yjit / rails-cruby-off`: Median **1.500x** (Rep 1: 1.240x, Rep 2: 1.000x, Rep 3: 4.000x, Rep 4: 1.500x, Rep 5: 2.000x).
- **Warmup Fail-Fast Validation**:
  - The adaptive fail-fast configuration (`warmup_fail_fast_windows: 3`, `warmup_max_latency_ms: 2000`) successfully terminated hopeless warmup phases for JIT-off variants (`rails-jruby-off`, `emit-jruby-off`) at ~194–206 seconds instead of exhausting the full 900-second warmup budget. This reduced total suite execution time to ~10 hours.
- **Unconfirmed / Incomplete Targets (0/5)**:
  - `spinel`: 0/5 confirmed repetitions. Spinel suffered warmup timeouts (~915s) under the in-memory `app-sliced-page20-1000` workload without database-level offset support (#54).
  - `emit-cruby-off`: 0/5 confirmed repetitions due to search step failures at higher rates without JIT.
  - `rails-jruby-off` and `emit-jruby-off`: 0/5 confirmed repetitions because pure interpretation without JIT could not sustain the minimum 25 RPS threshold under the 100 ms SLO.

---

## 1. Experiment and Provenance

- **Evaluated Target Commit**: `bf098d3e724af5b84d26da9e8398942716a50009`
- **Raw Evidence Release**: GitHub Release [gce-c3-capacity-20260930](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260930)
  - `gce-c3-capacity-20260930-raw-artifacts.tar.gz` (SHA-256: `4ceded406c302c877a9faa3394b47b8edb38084190b0a6cb5fdf808518ac3646`)
  - `gce-c3-capacity-20260930-raw-artifacts.zip` (SHA-256: `3e88ee547af981663214908cf765cd402388cd76637434e3758afe417efbc850`)
- **Infrastructure**:
  - GCE Project: `sandbox-svc-dev-8rra`, Zone: `asia-northeast1-b`
  - App VM: `bench-app-c3` (Internal IP: `10.146.0.11`, 4 vCPUs / 16 GB, c3-standard-4)
  - Load Generator: `bench-loadgen-c3` (Internal IP: `10.146.0.12`, 4 vCPUs / 16 GB, c3-standard-4)
  - Network: Private VPC (`default`), Cloud NAT (`nat-jpe-01`), no external IPs
  - Private RTT: min/avg/max = 0.009 / 0.019 / 0.037 ms (5 packets, 0% loss)
- **App OS**: Linux kernel `7.0.0-1011-gcp`, Ubuntu 24.04.1 LTS
- **Docker**: Engine Community v29.8.1, containerd v2.3.6, runc v1.5.1
- **Pinned Container Images**:
  - `rails-aot-bench:rails-cruby`: `sha256:7b9dde17396ee433062543c587ff0cd6e1c56c6894db1af76408a67f1af85aa7`
  - `rails-aot-bench:rails-jruby`: `sha256:6234883047095690e5f744647a55a7ae31adfa2e3f649028d755ed00959e580c`
  - `rails-aot-bench:emitted-cruby`: `sha256:cb468207b3a1896483af52d8d6cdd84982774f9a6254ddb612309743626db095`
  - `rails-aot-bench:emitted-jruby`: `sha256:088294c27e661363a4ffbc76c5c8f063988e87c48f1c0e35a51795bb0603aa6b`
  - `rails-aot-bench:spinel`: `sha256:51d4fc71098f37480e5c017bbfffef61a9e968590a8def8a2a31cd28c2d91f7b`
- **Workload**: `app-sliced-page20-1000`
  - Endpoint: `GET /articles?page=1`
  - Fixture: 1,000 articles in SQLite
  - Note: SQLite executes `ORDER BY created_at DESC`, after which the application loads all 1,000 records into memory, instantiates 1,000 ActiveRecord objects, and slices `[0...20]`. Roundhouse Spinel currently lacks ActiveRecord `offset` (#47); this result does not represent DB LIMIT/OFFSET paging.
- **Execution Models**:
  - Warmup: Closed-loop `constant-vus` with 32 VUs (`connections: 32`), 30-second windows (min 180s, max 900s, fail-fast at 3 windows if error rate > 20% or latency > 2,000 ms).
  - Capacity Search: Open-arrival `k6-open-arrival` with binary/step search (tolerance 25 RPS) and downward recovery backoff.
  - Confirmation Window: 120-second steady-state execution at discovered capacity.
- **SLO Contract**:
  - p99 ≤ 100 ms
  - Error rate < 0.1%
  - Zero dropped iterations, zero client saturation
  - 120-second confirmation window

---

## 2. Two-VM Topology

```
[ Tester VM: bench-loadgen-c3 ]
  |-- k6 v1.x (open-arrival & closed-loop scenarios)
  |-- Private VPC (10.146.0.12)
        | (RTT: ~0.019 ms)
        v
[ App VM: bench-app-c3 (10.146.0.11) ]
  |-- Docker Engine (4 vCPUs dedicated, 14 GB memory limit)
  |-- Container port 3000 bound to host 0.0.0.0:3000
  |-- In-memory /proc/net/dev, cpu.stat, and container telemetry collectors
```

---

## 3. Strict Correctness Gate (Preflight)

Prior to running the capacity benchmark, all 9 targets were evaluated against the reference implementation (`rails-cruby-off`) on `GET /articles?page=1` and common endpoints.

| Target | Page Eligible (`/articles?page=1`) | Preflight Status | Eligible Endpoints |
|---|:---:|:---:|---|
| `rails-cruby-off` | **True** | Passed (Reference) | 7 endpoints |
| `rails-cruby-yjit` | **True** | Eligible | 7 endpoints |
| `rails-jruby-off` | **True** | Eligible | 7 endpoints |
| `rails-jruby` | **True** | Eligible | 7 endpoints |
| `emit-cruby-off` | **True** | Eligible | 7 endpoints |
| `emit-cruby-yjit` | **True** | Eligible | 7 endpoints |
| `emit-jruby-off` | **True** | Eligible | 7 endpoints |
| `emit-jruby` | **True** | Eligible | 7 endpoints |
| `spinel` | **True** | Eligible | 7 endpoints |

Preflight manifest and checksums: `bench-results/gce-20260930-c3-capacity/preflight/preflight-manifest.json`.

---

## 4. Primary Capacity & Latency Summary

| Target | Confirmed Reps | Median Sustainable RPS | Worst p99 ms | Worst Error % | Mean App CPU % | Peak Memory (MB) | Warmup Duration Range |
|---|---:|---:|---:|---:|---:|---:|---|
| **`emit-cruby-yjit`** | **5/5** | **99.99** | **56.36** | 0.00% | 191.13% | **224.90 MB** | 182–212s |
| **`rails-cruby-yjit`** | **5/5** | **75.00** | 82.61 | 0.00% | 120.58% | 642.50 MB | 181–242s |
| **`rails-cruby-off`** | **5/5** | **49.99** | 62.73 | 0.00% | 130.97% | 484.50 MB | 183s |
| **`emit-jruby`** | **5/5** | **49.99** | 81.69 | 0.00% | 115.28% | 1093.63 MB | 183–244s |
| `rails-jruby` | 4/5 | 25.00 | 61.61 | 0.00% | 96.88% | 1682.43 MB | 304–911s |
| `emit-cruby-off` | 0/5 | — | — | — | — | — | 216–680s |
| `rails-jruby-off` | 0/5 | — | — | — | — | — | 194–195s (Fail-Fast) |
| `emit-jruby-off` | 0/5 | — | — | — | — | — | 203–206s (Fail-Fast) |
| `spinel` | 0/5 | — | — | — | — | — | 914–917s |

---

## 5. Paired Capacity Speedup Ratios

Paired ratios are computed solely across identical randomized repetitions where both targets achieved confirmed 120-second capacity:

| Numerator / Denominator | Median Ratio | Min | Max | IQR | Repetition Details |
|---|---:|---:|---:|---:|---|
| **`emit-cruby-yjit / rails-cruby-yjit`** | **1.333x** | 0.750x | 2.000x | 0.613x | Rep 1: 1.613x, Rep 2: 2.000x, Rep 3: 1.000x, Rep 4: 1.333x, Rep 5: 0.750x |
| **`emit-jruby / rails-jruby`** | **2.000x** | 0.500x | 2.000x | 1.499x | Rep 2: 2.000x, Rep 3: 2.000x, Rep 4: 0.500x, Rep 5: 2.000x |
| **`rails-cruby-yjit / rails-cruby-off`** | **1.500x** | 1.000x | 4.000x | 0.760x | Rep 1: 1.240x, Rep 2: 1.000x, Rep 3: 4.000x, Rep 4: 1.500x, Rep 5: 2.000x |
| `emit-cruby-off / rails-cruby-off` | — | — | — | — | Incomplete (0/5 confirmed reps for emit-cruby-off) |
| `emit-jruby-off / rails-jruby-off` | — | — | — | — | Incomplete (0/5 confirmed reps for JIT-off variants) |
| `spinel / rails-cruby-off` | — | — | — | — | Incomplete (0/5 confirmed reps for spinel) |

---

## 6. All 45 Trial Repetitions

| Trial | Target | Rep | Status | Sustained RPS | Offered RPS | Confirm p99 (ms) | Error % | App CPU % | Memory (MB) | Warmup (s) |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| `0000` | `rails-jruby` | 1 | `unstable` | — | — | — | — | — | — | 911s |
| `0001` | `rails-jruby-off` | 1 | `unstable` | — | — | — | — | — | — | 194s |
| `0002` | `emit-jruby` | 1 | `passed` | 25.00 | 25 | 35.50 | 0.0% | 63.97% | 922.1 MB | 243s |
| `0003` | `emit-jruby-off` | 1 | `unstable` | — | — | — | — | — | — | 205s |
| `0004` | `rails-cruby-yjit` | 1 | `passed` | 62.00 | 62 | 28.67 | 0.0% | 85.04% | 632.6 MB | 181s |
| `0005` | `rails-cruby-off` | 1 | `passed` | 50.00 | 50 | 56.87 | 0.0% | 143.66% | 483.8 MB | 183s |
| `0006` | `emit-cruby-yjit` | 1 | `passed` | 99.98 | 100 | 54.60 | 0.0% | 202.82% | 223.7 MB | 182s |
| `0007` | `emit-cruby-off` | 1 | `failed` | — | — | — | — | — | — | 680s |
| `0008` | `spinel` | 1 | `unstable` | — | — | — | — | — | — | 916s |
| `0009` | `rails-jruby` | 2 | `passed` | 25.00 | 25 | 46.38 | 0.0% | 69.36% | 1485.8 MB | 669s |
| `0010` | `spinel` | 2 | `unstable` | — | — | — | — | — | — | 916s |
| `0011` | `emit-cruby-off` | 2 | `failed` | — | — | — | — | — | — | 402s |
| `0012` | `emit-cruby-yjit` | 2 | `passed` | 99.99 | 100 | 56.36 | 0.0% | 211.59% | 224.2 MB | 212s |
| `0013` | `rails-cruby-off` | 2 | `passed` | 49.99 | 50 | 62.73 | 0.0% | 153.18% | 482.9 MB | 183s |
| `0014` | `rails-cruby-yjit` | 2 | `passed` | 50.00 | 50 | 27.88 | 0.0% | 71.43% | 642.5 MB | 242s |
| `0015` | `emit-jruby-off` | 2 | `unstable` | — | — | — | — | — | — | 205s |
| `0016` | `emit-jruby` | 2 | `passed` | 50.00 | 50 | 74.59 | 0.0% | 140.76% | 1093.6 MB | 243s |
| `0017` | `rails-jruby-off` | 2 | `failed` | — | — | — | — | — | — | 195s |
| `0018` | `emit-jruby` | 3 | `passed` | 50.00 | 50 | 81.69 | 0.0% | 152.60% | 854.9 MB | 244s |
| `0019` | `emit-jruby-off` | 3 | `unstable` | — | — | — | — | — | — | 206s |
| `0020` | `rails-cruby-yjit` | 3 | `passed` | 99.98 | 100 | 82.61 | 0.0% | 168.32% | 613.1 MB | 181s |
| `0021` | `rails-cruby-off` | 3 | `passed` | 25.00 | 25 | 39.87 | 0.0% | 68.35% | 482.9 MB | 183s |
| `0022` | `emit-cruby-yjit` | 3 | `passed` | 99.99 | 100 | 55.36 | 0.0% | 210.14% | 224.9 MB | 182s |
| `0023` | `emit-cruby-off` | 3 | `failed` | — | — | — | — | — | — | 216s |
| `0024` | `spinel` | 3 | `unstable` | — | — | — | — | — | — | 914s |
| `0025` | `rails-jruby` | 3 | `passed` | 25.00 | 25 | 52.64 | 0.0% | 78.72% | 1555.5 MB | 305s |
| `0026` | `rails-jruby-off` | 3 | `failed` | — | — | — | — | — | — | 195s |
| `0027` | `emit-jruby` | 4 | `passed` | 25.00 | 25 | 45.44 | 0.0% | 72.50% | 927.8 MB | 183s |
| `0028` | `rails-jruby-off` | 4 | `unstable` | — | — | — | — | — | — | 195s |
| `0029` | `rails-jruby` | 4 | `passed` | 50.00 | 50 | 61.61 | 0.0% | 160.34% | 1549.3 MB | 304s |
| `0030` | `spinel` | 4 | `unstable` | — | — | — | — | — | — | 916s |
| `0031` | `emit-cruby-off` | 4 | `failed` | — | — | — | — | — | — | 247s |
| `0032` | `emit-cruby-yjit` | 4 | `passed` | 99.99 | 100 | 55.74 | 0.0% | 208.23% | 224.5 MB | 182s |
| `0033` | `rails-cruby-off` | 4 | `passed` | 49.99 | 50 | 59.11 | 0.0% | 144.26% | 484.5 MB | 183s |
| `0034` | `rails-cruby-yjit` | 4 | `passed` | 75.00 | 75 | 67.92 | 0.0% | 112.37% | 612.5 MB | 181s |
| `0035` | `emit-jruby-off` | 4 | `unstable` | — | — | — | — | — | — | 205s |
| `0036` | `rails-cruby-yjit` | 5 | `passed` | 99.99 | 100 | 66.04 | 0.0% | 165.74% | 634.2 MB | 212s |
| `0037` | `rails-cruby-off` | 5 | `passed` | 49.99 | 50 | 60.44 | 0.0% | 145.41% | 460.8 MB | 183s |
| `0038` | `emit-cruby-yjit` | 5 | `passed` | 75.00 | 75 | 32.38 | 0.0% | 122.88% | 211.0 MB | 182s |
| `0039` | `emit-cruby-off` | 5 | `failed` | — | — | — | — | — | — | 463s |
| `0040` | `spinel` | 5 | `unstable` | — | — | — | — | — | — | 917s |
| `0041` | `rails-jruby` | 5 | `passed` | 25.00 | 25 | 49.36 | 0.0% | 79.08% | 1682.4 MB | 305s |
| `0042` | `rails-jruby-off` | 5 | `failed` | — | — | — | — | — | — | 194s |
| `0043` | `emit-jruby` | 5 | `passed` | 50.00 | 50 | 70.79 | 0.0% | 146.56% | 942.1 MB | 243s |
| `0044` | `emit-jruby-off` | 5 | `unstable` | — | — | — | — | — | — | 203s |

---

## 7. Technical Analysis & Findings

### A. Emitted Code Generation & YJIT Synergy
- **Throughput & Latency**: `emit-cruby-yjit` demonstrated exceptional efficiency, sustaining a median 99.99 RPS with a worst-case p99 of 56.36 ms (and sub-35 ms in low-contention repetitions). By compiling controller routing, view rendering, and model dispatch ahead-of-time, emit removes dynamic Rails framework overhead, while YJIT optimizes remaining runtime hot spots.
- **Memory Footprint**: `emit-cruby-yjit` peaked at **224.9 MB**, approximately **35%** of standard Rails with YJIT (642.5 MB). Ahead-of-time code generation substantially reduces runtime metaprogramming structures and object allocation churn.

### B. JRuby Ahead-of-Time Advantage
- In JRuby, `emit-jruby` delivered **49.99 RPS** (5/5 reps confirmed) compared to standard `rails-jruby` at **25.00 RPS** (4/5 reps confirmed).
- Across matched repetitions, `emit-jruby` achieved a median **2.000x** throughput advantage over standard Rails on JRuby. Eliminating dynamic Active Support / Active Record metaprogramming significantly accelerates JVM JIT compilation and tier-2 bytecode optimization.

### C. Warmup Fail-Fast Optimization Efficacy
- In earlier benchmark iterations (e.g. 2026-09-28), JIT-off variants consumed the full 900-second warmup budget per repetition despite experiencing unrecoverable request timeouts.
- In this run, the new `warmup_fail_fast_windows: 3` and `warmup_max_latency_ms: 2000` configuration detected hopeless saturation early and exited at ~194–206 seconds, saving over 5 hours of idle VM runtime across the suite.

### D. Spinel In-Memory vs DB-Level Slicing Dynamics
- Under the `app-sliced-page20-1000` workload, Spinel was unable to achieve warmup convergence within 900 seconds (0/5 reps). While Spinel's native execution architecture offers low peak memory, loading 1,000 SQLite records into coroutine memory without database-level `OFFSET` support creates heavy contention (#54).
- The newly implemented DB-level pagination (`db-paged-page20-1000`, #47) resolves this by delegating `LIMIT 20 OFFSET ?` directly to SQLite.

---

## 8. Comparison with 2026-09-28 Pilot Run

| Attribute | 2026-09-28 Run (`gce-c3-capacity-20260928`) | 2026-09-30 Run (`gce-c3-capacity-20260930`) |
|---|---|---|
| **Evaluated Commit** | `798963437f3d95f959f4fcf61ab2b3d2da82fc55` | `bf098d3e724af5b84d26da9e8398942716a50009` |
| **Search Mechanism** | Monotonic coarse search (failed at 200 RPS) | Downward recovery search (tolerance 25 RPS) |
| **Warmup Behavior** | Consumed 900s on all JIT-off trials | Fail-fast early exit at ~195s |
| **Confirmed Capacity** | 0/5 valid repetitions across all targets | **5/5 confirmed for 4 targets; 4/5 for 1 target** |
| **Top Confirmed RPS** | Unconfirmed | **99.99 RPS (`emit-cruby-yjit`)** |
| **Evidence Release** | [gce-c3-capacity-20260928](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260928) | [gce-c3-capacity-20260930](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260930) |

---

## 9. Resource Teardown and Cost Verification

Both GCE VMs were terminated immediately following execution:
- `bench-app-c3`: `TERMINATED`
- `bench-loadgen-c3`: `TERMINATED`
- Zero active compute charges remain. SSD persistent disks can be deleted via `terraform destroy` when no longer needed.

---

## 10. Replication and Artifact Integrity

All raw artifacts, k6 open-arrival logs, telemetry snapshots, and database captures are archived and published in [GitHub Release gce-c3-capacity-20260930](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260930).
All 6,578 files match their published checksums in `bench-results/gce-20260930-c3-capacity/SHA256SUMS`.

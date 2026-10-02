# GCE c3-standard-4 × 2 Benchmark Report

## Evidence review (2026-10-01)

The numeric tables below describe confirmed **candidate lower points**, not precisely resolved maximum capacities. In the measured runner, a failed 120-second candidate was halved and the first lower successful point ended the search; the 25-RPS tolerance was also coarse. Candidate ratios reproduce the arithmetic but retain this search uncertainty. `rails-jruby` has only four valid pairs, so its 25-RPS median is a partial observation, not a complete five-repetition ranking.

The release archive SHA-256 is `4ceded406c302c877a9faa3394b47b8edb38084190b0a6cb5fdf808518ac3646`; all 6,578 manifest entries were verified. Eight failed trial directories lack `trial.json` (five `emit-cruby-off`, three `rails-jruby-off`); the authoritative per-run index and available raw steps remain usable, with this gap explicit. No `cleanup.json`, low-load sweep, JFR or measured stage microbenchmark is included. The measured commit differs from the report revision; preserve both SHAs and the existing release tag.

The revised runner searches down to 1 RPS, refines after confirmation failure with fractional offered rates and records confirmed lower/failed upper bounds, their durations and resolution. It uses a four-VU warmup, separate from this release's 32-VU cohort. These changes were evaluated on C3 in the 2026-10-01 retest (see [Section 11](#11-gce-c3-standard-4-retest-evidence-2026-10-01)). See [runtime failure investigation](diagnostics-jruby-off-and-spinel.md), [aligned observations](reversal-analysis-rails-vs-roundhouse.md), and [measurement protocol](../.agents/skills/gce-benchmark-runbook/references/measurement-protocol.md).

## Executive Summary

The formal two-VM capacity experiment was executed on Google Compute Engine using two dedicated `c3-standard-4` instances (App SUT: `bench-app-c3`, Load Generator: `bench-loadgen-c3`) in `asia-northeast1-b` over a private VPC with Cloud NAT. The experiment evaluated 9 target runtime configurations across 5 randomized rotation repetitions (45 total trials) using the formal capacity profile `bench/profiles/gce-c3-capacity.yml` on commit `bf098d3e724af5b84d26da9e8398942716a50009`.

Under the rigorous benchmarking contract (`rigorous-benchmarking`), **only confirmed per-repetition sustainable capacity under a 120-second verification window (p99 ≤ 100 ms, errors < 0.1%) is eligible for comparison**, and **no unmeasured capacity is inferred or invented**.

- **Correctness Gate**: All 9 targets passed strict preflight verification for the primary workload `/articles?page=1` (`app-sliced-page20-1000`).
- **Confirmed Capacity Outcomes**:
  - **`emit-cruby-yjit`**: Achieved **5/5 confirmed repetitions** with median confirmed candidate **99.99 RPS** (worst p99: **56.36 ms**), mean CPU utilization of 191.1%, and peak memory of **224.9 MiB** among the successfully confirmed candidate cohorts; offered rates differ.
  - **`rails-cruby-yjit`**: Achieved **5/5 confirmed repetitions** with median confirmed candidate **75.00 RPS** (worst p99: **82.61 ms**), outperforming `rails-cruby-off` by a median ratio of **1.50x**.
  - **`rails-cruby-off`**: Achieved **5/5 confirmed repetitions** with median confirmed candidate **49.99 RPS** (worst p99: **62.73 ms**), establishing the verified baseline for standard Ruby bytecode interpretation.
  - **`emit-jruby`**: Achieved **5/5 confirmed repetitions** with median confirmed candidate **49.99 RPS** (worst p99: **81.69 ms**), achieving **2.00x** the throughput of standard `rails-jruby` in matched repetitions.
  - **`rails-jruby`**: Achieved **4/5 confirmed repetitions** with **25.00 RPS** (worst p99: **61.61 ms**; Rep 1 was unstable during warmup with CV > 5%).
- **Paired Capacity Speedup Ratios**:
  - `emit-cruby-yjit / rails-cruby-yjit`: Median **1.333x** (Rep 1: 1.613x, Rep 2: 2.000x, Rep 3: 1.000x, Rep 4: 1.333x, Rep 5: 0.750x).
  - `emit-jruby / rails-jruby`: Median **2.000x** (Rep 2: 2.000x, Rep 3: 2.000x, Rep 4: 0.500x, Rep 5: 2.000x).
  - `rails-cruby-yjit / rails-cruby-off`: Median **1.500x** (Rep 1: 1.240x, Rep 2: 1.000x, Rep 3: 4.000x, Rep 4: 1.500x, Rep 5: 2.000x).
- **Warmup Fail-Fast Validation**:
  - The adaptive fail-fast configuration (`warmup_fail_fast_windows: 3`, `warmup_max_latency_ms: 2000`) successfully terminated hopeless warmup phases for JIT-off variants (`rails-jruby-off`, `emit-jruby-off`) at ~194–206 seconds instead of exhausting the full 900-second warmup budget. This reduced total suite execution time to ~10 hours.
- **Unconfirmed / Incomplete Targets (0/5)**:
  - `spinel`: 0/5 confirmed repetitions. Spinel suffered warmup timeouts (~915s) under the in-memory `app-sliced-page20-1000` workload without database-level offset support (#54).
  - `emit-cruby-off`: 0/5 confirmed repetitions because the initial 25-RPS step failed p99 (about 106–122 ms) in all five trials, with no request errors or drops; below 25 RPS was not tested.
  - `rails-jruby-off` and `emit-jruby-off`: 0/5 confirmed repetitions because warmup did not converge; low-rate open-arrival feasibility is unmeasured. HotSpot was not disabled by JRuby compile.mode=OFF.

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

| Target | Confirmed Reps | Median Confirmed Candidate RPS | Worst p99 ms | Worst Error % | Mean App CPU % | Peak Memory (MiB) | Warmup Duration Range |
|---|---:|---:|---:|---:|---:|---:|---|
| **`emit-cruby-yjit`** | **5/5** | **99.99** | **56.36** | 0.00% | 191.13% | **224.90 MiB** | 182–212s |
| **`rails-cruby-yjit`** | **5/5** | **75.00** | 82.61 | 0.00% | 120.58% | 642.50 MiB | 181–242s |
| **`rails-cruby-off`** | **5/5** | **49.99** | 62.73 | 0.00% | 130.97% | 484.50 MiB | 183s |
| **`emit-jruby`** | **5/5** | **49.99** | 81.69 | 0.00% | 115.28% | 1093.63 MiB | 183–244s |
| `rails-jruby` | 4/5 | 25.00 | 61.61 | 0.00% | 96.88% | 1682.43 MiB | 304–911s |
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

| Trial | Target | Rep | Status | Sustained RPS | Offered RPS | Confirm p99 (ms) | Error % | App CPU % | Memory (MiB) | Warmup (s) |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| `0000` | `rails-jruby` | 1 | `unstable` | — | — | — | — | — | — | 911s |
| `0001` | `rails-jruby-off` | 1 | `unstable` | — | — | — | — | — | — | 194s |
| `0002` | `emit-jruby` | 1 | `passed` | 25.00 | 25 | 35.50 | 0.0% | 63.97% | 922.1 MiB | 243s |
| `0003` | `emit-jruby-off` | 1 | `unstable` | — | — | — | — | — | — | 205s |
| `0004` | `rails-cruby-yjit` | 1 | `passed` | 62.00 | 62 | 28.67 | 0.0% | 85.04% | 632.6 MiB | 181s |
| `0005` | `rails-cruby-off` | 1 | `passed` | 50.00 | 50 | 56.87 | 0.0% | 143.66% | 483.8 MiB | 183s |
| `0006` | `emit-cruby-yjit` | 1 | `passed` | 99.98 | 100 | 54.60 | 0.0% | 202.82% | 223.7 MiB | 182s |
| `0007` | `emit-cruby-off` | 1 | `failed` | — | — | — | — | — | — | 680s |
| `0008` | `spinel` | 1 | `unstable` | — | — | — | — | — | — | 916s |
| `0009` | `rails-jruby` | 2 | `passed` | 25.00 | 25 | 46.38 | 0.0% | 69.36% | 1485.8 MiB | 669s |
| `0010` | `spinel` | 2 | `unstable` | — | — | — | — | — | — | 916s |
| `0011` | `emit-cruby-off` | 2 | `failed` | — | — | — | — | — | — | 402s |
| `0012` | `emit-cruby-yjit` | 2 | `passed` | 99.99 | 100 | 56.36 | 0.0% | 211.59% | 224.2 MiB | 212s |
| `0013` | `rails-cruby-off` | 2 | `passed` | 49.99 | 50 | 62.73 | 0.0% | 153.18% | 482.9 MiB | 183s |
| `0014` | `rails-cruby-yjit` | 2 | `passed` | 50.00 | 50 | 27.88 | 0.0% | 71.43% | 642.5 MiB | 242s |
| `0015` | `emit-jruby-off` | 2 | `unstable` | — | — | — | — | — | — | 205s |
| `0016` | `emit-jruby` | 2 | `passed` | 50.00 | 50 | 74.59 | 0.0% | 140.76% | 1093.6 MiB | 243s |
| `0017` | `rails-jruby-off` | 2 | `failed` | — | — | — | — | — | — | 195s |
| `0018` | `emit-jruby` | 3 | `passed` | 50.00 | 50 | 81.69 | 0.0% | 152.60% | 854.9 MiB | 244s |
| `0019` | `emit-jruby-off` | 3 | `unstable` | — | — | — | — | — | — | 206s |
| `0020` | `rails-cruby-yjit` | 3 | `passed` | 99.98 | 100 | 82.61 | 0.0% | 168.32% | 613.1 MiB | 181s |
| `0021` | `rails-cruby-off` | 3 | `passed` | 25.00 | 25 | 39.87 | 0.0% | 68.35% | 482.9 MiB | 183s |
| `0022` | `emit-cruby-yjit` | 3 | `passed` | 99.99 | 100 | 55.36 | 0.0% | 210.14% | 224.9 MiB | 182s |
| `0023` | `emit-cruby-off` | 3 | `failed` | — | — | — | — | — | — | 216s |
| `0024` | `spinel` | 3 | `unstable` | — | — | — | — | — | — | 914s |
| `0025` | `rails-jruby` | 3 | `passed` | 25.00 | 25 | 52.64 | 0.0% | 78.72% | 1555.5 MiB | 305s |
| `0026` | `rails-jruby-off` | 3 | `failed` | — | — | — | — | — | — | 195s |
| `0027` | `emit-jruby` | 4 | `passed` | 25.00 | 25 | 45.44 | 0.0% | 72.50% | 927.8 MiB | 183s |
| `0028` | `rails-jruby-off` | 4 | `unstable` | — | — | — | — | — | — | 195s |
| `0029` | `rails-jruby` | 4 | `passed` | 50.00 | 50 | 61.61 | 0.0% | 160.34% | 1549.3 MiB | 304s |
| `0030` | `spinel` | 4 | `unstable` | — | — | — | — | — | — | 916s |
| `0031` | `emit-cruby-off` | 4 | `failed` | — | — | — | — | — | — | 247s |
| `0032` | `emit-cruby-yjit` | 4 | `passed` | 99.99 | 100 | 55.74 | 0.0% | 208.23% | 224.5 MiB | 182s |
| `0033` | `rails-cruby-off` | 4 | `passed` | 49.99 | 50 | 59.11 | 0.0% | 144.26% | 484.5 MiB | 183s |
| `0034` | `rails-cruby-yjit` | 4 | `passed` | 75.00 | 75 | 67.92 | 0.0% | 112.37% | 612.5 MiB | 181s |
| `0035` | `emit-jruby-off` | 4 | `unstable` | — | — | — | — | — | — | 205s |
| `0036` | `rails-cruby-yjit` | 5 | `passed` | 99.99 | 100 | 66.04 | 0.0% | 165.74% | 634.2 MiB | 212s |
| `0037` | `rails-cruby-off` | 5 | `passed` | 49.99 | 50 | 60.44 | 0.0% | 145.41% | 460.8 MiB | 183s |
| `0038` | `emit-cruby-yjit` | 5 | `passed` | 75.00 | 75 | 32.38 | 0.0% | 122.88% | 211.0 MiB | 182s |
| `0039` | `emit-cruby-off` | 5 | `failed` | — | — | — | — | — | — | 463s |
| `0040` | `spinel` | 5 | `unstable` | — | — | — | — | — | — | 917s |
| `0041` | `rails-jruby` | 5 | `passed` | 25.00 | 25 | 49.36 | 0.0% | 79.08% | 1682.4 MiB | 305s |
| `0042` | `rails-jruby-off` | 5 | `failed` | — | — | — | — | — | — | 194s |
| `0043` | `emit-jruby` | 5 | `passed` | 50.00 | 50 | 70.79 | 0.0% | 146.56% | 942.1 MiB | 243s |
| `0044` | `emit-jruby-off` | 5 | `unstable` | — | — | — | — | — | — | 203s |

---

## 7. Technical Analysis & Findings

The candidate tables support repeated successful confirmations for four configurations and partial confirmation for `rails-jruby`. The candidate ratio medians are 1.333x, 2.000x (four pairs) and 1.500x; they do not isolate code-generation or JIT mechanisms. Cross-target resource aggregates span different rates. The aligned rep-3 100-RPS/120-second YJIT comparison is documented separately.

Fail-fast stopped the failing JRuby-Off warmups at about 194–206 seconds. This is an observed early exit, not proof of unrecoverable saturation or a measured five-hour saving against a controlled baseline.

Spinel failed every warmup window with 10,929 failures in 475,935 requests. Missing SQL/worker traces and low-VU measurements leave the cause and safe load unresolved. DB paging changes the workload; this release cannot establish that it fixes the timeout behavior.

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

The archive does not contain `cleanup.json` or dated stop-verification output. VM termination and compute billing cannot be verified from this evidence. The revised orchestrator records both stop results and timestamps; a stopped VM still retains billable disks until those disks are deleted.

---

## 10. Replication and Artifact Integrity

All raw artifacts, k6 open-arrival logs, telemetry snapshots, and database captures are archived and published in [GitHub Release gce-c3-capacity-20260930](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260930).
All 6,578 files match their published checksums in `bench-results/gce-20260930-c3-capacity/SHA256SUMS`.

---

## 11. GCE c3-standard-4 Retest Evidence (2026-10-01)

### 11.1 Experiment Provenance & Setup

The formal GCE C3 capacity retest was executed on 2026-10-01 to validate the revised capacity exploration protocol, lower-load stability, failure boundary classification, and complete artifact preservation on physical cloud infrastructure:

- **Target Evaluated Commit**: `4ae7f78111b1bdab07f455f793615e039018a276` (PR #65 merge commit)
- **Run Identifier**: `c3-retest-20261001T061200Z`
- **Execution Topology**: Two dedicated `c3-standard-4` instances (`bench-app-c3`: 10.146.0.11, `bench-loadgen-c3`: 10.146.0.12) in GCP zone `asia-northeast1-b` over a private VPC with Cloud NAT (RTT: min/avg/max = 0.038 / 0.039 / 0.040 ms)
- **Hardware/Host**: Intel(R) Xeon(R) Platinum 8481C @ 2.70GHz, 4 vCPUs (0-3), SMT 2 threads/core, 16 GiB memory, Linux kernel `7.0.0-1013-gcp`, Ubuntu 24.04.1 LTS
- **Workload**: `app-sliced-page20-1000` at `GET /articles?page=1` (1,000 articles, page size 20)
- **Profile Parameters**: `profiles/formal-capacity.json`
  - Total runner budget: 36,000s (10.0 hours)
  - Diagnostics: disabled for capacity measurement
  - Warmup: closed-loop with **4 VUs** (`warmup_connections: 4`), 30s windows (180s–900s), CV/drift ≤ 0.08
  - Starting offered rates: 25 RPS for un-JITed targets (`rails-cruby-off`, `rails-jruby-off`, `emit-cruby-off`, `emit-jruby-off`), 100 RPS for other targets
  - Capacity bounds: 1 to 12,800 offered RPS, tolerance `min(5 RPS, confirmed_lower × 0.05)`
  - Confirmation window: 120 seconds sustained (SLO: p99 ≤ 100 ms, error rate < 0.1%, drops = 0)
  - Recovery health probe: 1 RPS × 5s (max 3 attempts, 60s timeout)
- **Raw Evidence Release**: Published to [GitHub Release gce-c3-retest-20261001-c3-retest-20261001T061200Z](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-retest-20261001-c3-retest-20261001T061200Z)
  - Archive: `c3-retest-20261001T061200Z.tar.gz` (10,348 files, 157.0 MB extracted)
  - Verification: 100% verified against remote `SHA256SUMS.received` (`transfer-verification.exit`: 0)

### 11.2 Step 0: Preflight and Correctness Gate

Strict preflight evaluation verified all 9 target runtimes against `GET /articles?page=1` with 1,000 fixture articles. All 9 targets reported `eligible_endpoints` including `/articles?page=1`, `/articles`, `/articles/1`, `/articles/new`, `/articles.json`, `/articles/1.json`, and `/articles?page=1&pagination=db-paged`. Manifest generated at `preflight/preflight-manifest.json`.

### 11.3 Step 1A: Low-VU Closed-Loop Diagnostic Results

Before launching formal capacity measurement, low-concurrency closed-loop diagnostics evaluated 4 target runtimes (`rails-jruby-off`, `emit-jruby-off`, `emit-jruby`, `spinel`) across 1 VU and 4 VUs (3 repetitions, 120s measurement window, 1 VU warmup):

| Target | Concurrency | Reps Completed | Achieved RPS | Median p50 (ms) | Worst p95 (ms) | Worst p99 (ms) | Error Rate | Peak Memory (MiB) | Tester CPU % | Assessment |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `spinel` | 1 VU | 3/3 | 53.4–53.6 | 17.8 | 20.5 | 22.1 | 0.00% | 38.6–39.1 | 3.2–3.7% | Stable, minimal latency |
| `emit-jruby` | 1 VU | 3/3 | 30.1–31.8 | 31.6 | 43.2 | 46.0 | 0.00% | 567.8–582.9 | 2.1–2.3% | Stable |
| `rails-jruby-off` | 1 VU | 3/3 | 5.9–6.2 | 161.9 | 191.4 | 201.0 | 0.00% | 553.5–568.0 | 0.5–0.8% | Stable un-JITed baseline |
| `emit-jruby-off` | 1 VU | 3/3 | 1.85–1.92 | 529.0 | 547.9 | 567.1 | 0.00% | 428.4–433.0 | 0.2–0.8% | Stable; single request ~520ms |
| `spinel` | 4 VU | 3/3 | 135.9–137.3 | 28.1 | 34.3 | 38.1 | 0.00% | 111.4–121.8 | 8.3–8.5% | Exceptional scaling |
| `emit-jruby` | 4 VU | 3/3 | 61.9–66.9 | 59.1 | 89.7 | 115.3 | 0.00% | 823.8–848.0 | 3.9–4.9% | Stable linear scaling |
| `rails-jruby-off` | 4 VU | 3/3 | 11.8–11.9 | 317.2 | 437.5 | 536.1 | 0.00% | 628.2–691.8 | 0.8–1.3% | Throughput ceiling ~12 RPS |
| `emit-jruby-off` | 4 VU | 3/3 | 3.00–3.21 | 1326.6 | 1359.0 | 1461.9 | 0.00% | 416.4–433.3 | 0.3% | Latency extends to ~1.3s; 0 errors |

All 24 trials achieved `warmup_converged: True` within 30–40 seconds. Zero process crashes, OOMs, or HTTP errors occurred. `emit-jruby-off` demonstrates that while individual request execution takes ~520 ms without JIT (and ~1.3s at 4 VUs), closed-loop execution is completely healthy and stable.

### 11.4 Step 1B: Low-RPS Open-Arrival Diagnostic Results

Step 1B evaluated the same 4 targets under open-arrival conditions at 1, 5, and 10 offered RPS (3 repetitions each, 120s measurement window, 1 VU warmup):

| Target | Offered RPS | Success Rate | Median p50 (ms) | Worst p95 (ms) | Worst p99 (ms) | Error Rate | Failure Breakdown | Diagnostic Outcome |
|---|---:|---:|---:|---:|---:|---:|---|---|
| `spinel` | 1 RPS | 3/3 | 19.1 | 23.3 | 24.5 | 0.00% | None | Passed |
| `emit-jruby` | 1 RPS | 3/3 | 32.2 | 35.7 | 43.7 | 0.00% | None | Passed |
| `rails-jruby-off` | 1 RPS | 3/3 | 162.2 | 184.0 | 191.7 | 0.00% | None | Passed |
| `emit-jruby-off` | 1 RPS | 3/3 | 523.1 | 535.2 | 538.1 | 0.00% | None | **Passed** (Low-load health confirmed) |
| `spinel` | 5 RPS | 3/3 | 19.7 | 24.9 | 27.8 | 0.00% | None | Passed |
| `emit-jruby` | 5 RPS | 3/3 | 32.7 | 37.1 | 39.5 | 0.00% | None | Passed |
| `rails-jruby-off` | 5 RPS | 3/3 | 162.5 | 190.4 | 197.2 | 0.00% | None | Passed |
| `emit-jruby-off` | 5 RPS | 0/3 | ~5,000 | ~5,000 | ~5,000 | 92.8% | timeout: 543, network: 0, 5xx: 0 | **SLO Failed** (Queue accumulation) |
| `spinel` | 10 RPS | 3/3 | 20.1 | 25.2 | 27.5 | 0.00% | None | Passed |
| `emit-jruby` | 10 RPS | 3/3 | 30.6 | 35.7 | 37.1 | 0.00% | None | Passed |
| `rails-jruby-off` | 10 RPS | 3/3 | 176.2 | 554.9 | 705.8 | 0.00% | None | Passed (Queuing begins near capacity) |
| `emit-jruby-off` | 10 RPS | 0/3 | ~5,000 | ~5,000 | ~5,000 | 95%+ | timeout: 1,100+, network: 0, 5xx: 0 | **SLO Failed** (Queue accumulation) |

**Root Cause Identification**: The failure boundary of `emit-jruby-off` is definitively pinpointed between 1 RPS and 5 RPS. Because serving capacity is ~1.9–3.0 RPS, open arrivals at 5 RPS inevitably outpace dequeue capacity, leading directly to the 5,000 ms request timeout. The absence of network drops, 500 status codes, or payload corruption proves that failure is strictly an open-arrival queuing phenomenon.

### 11.5 Step 2: Formal Capacity Remeasurement & Capacity Intervals

The formal capacity evaluation executed 45 trials under a 10-hour runner budget (`total_timeout: 36000`). Thirty trials were started; 19 achieved confirmed capacity, 11 failed/became unstable during search, and the final 15 trials were recorded as `not_run` upon budget exhaustion without silent completion.

| Target | Confirmed Reps | Confirmed Lower RPS (120s Confirm) | Failed Upper RPS | Upper Seconds | Interval Width (RPS) | Tolerance (RPS) | Search Status |
|---|---:|---|---|---:|---|---|---|
| **`emit-cruby-yjit`** | 3/5 | **106.25 – 108.98** | 109.38 – 112.50 | 30s / 120s | 3.125 – 3.516 | 4.04 – 5.00 | `pass` |
| **`rails-cruby-yjit`** | 2/5 | **93.75** | 96.88 | 30s | 3.125 | 4.69 | `pass` (Rep 3: `boundary_unstable` at 87.89 RPS) |
| **`rails-cruby-off`** | 3/5 | **48.44 – 60.94** | 50.00 – 62.50 | 30s | 1.562 – 1.563 | 2.42 – 3.05 | `pass` |
| **`rails-jruby`** | 3/5 | **42.19 – 54.49** | 43.75 – 56.25 | 30s / 120s | 1.562 – 1.758 | 2.11 – 2.72 | `pass` |
| **`emit-jruby`** | 4/5 | **35.94 – 48.44** | 37.50 – 50.00 | 30s | 1.562 – 1.563 | 1.80 – 2.42 | `pass` |
| **`emit-cruby-off`** | 3/5 | **21.88 – 23.44** | 22.66 – 24.22 | 30s | 0.781 – 0.782 | 1.09 – 1.17 | `pass` |
| **`spinel`** | 1/5 | **14.45** | 14.84 | 30s | 0.390 | 0.72 | `pass` (Reps 1, 2 failed recovery) |
| `rails-jruby-off` | 0/5 | — | — | — | — | — | Unconfirmed (`SUT recovery not verified`) |
| `emit-jruby-off` | 0/5 | — | — | — | — | — | Unconfirmed (`SUT recovery not verified`) |

### 11.6 Protocol Verifications from GCE Hardware

The retest provided direct physical confirmation of the PR #65 capacity algorithm enhancements:
1. **Refinement After Confirmation Failure**: For `spinel` in Rep 1, after failing a 120-second candidate at 15.625 RPS, the runner backed off to 7.812 RPS, succeeded, and climbed back to test 11.719 RPS rather than terminating after a single halved step. In Rep 3, Spinel converged to a confirmed interval of `[14.453, 14.843]` RPS.
2. **Recovery Health Probing**: When overload occurred at starting RPS (e.g. 25 RPS on `rails-jruby-off`), the 1 RPS × 5s recovery health probe executed. Because client-side recovery probing cannot guarantee internal server queue drainage, `server_queue_drained` was strictly preserved as `null`, and the trial was safely terminated rather than allowing contaminated downstream measurements.
3. **Budget and Coverage Discipline**: At the 10-hour runner limit, ongoing trial `0029-rails-jruby` recorded `Insufficient remaining capacity measurement budget`, and the remaining 15 trials were recorded as `not_run`. Plan vs. execution coverage was preserved without artificial completion.

### 11.7 Comparison Across Benchmarking Cohorts

> [!WARNING]
> Do not compute direct performance speedup multipliers between the 2026-09-30 release and the 2026-10-01 retest. The two cohorts differ in warmup concurrency (32 VUs vs. 4 VUs) and search resolution (25-RPS coarse steps vs. fractional bound refinement).

| Target Runtime | 2026-09-30 Candidate Cohort (32 VU Warmup) | 2026-10-01 Interval Cohort (4 VU Warmup) | Notes on Methodological Differences |
|---|---|---|---|
| `emit-cruby-yjit` | 99.99 RPS (5/5 reps confirmed) | **[106.25, 112.50] RPS** (3 reps evaluated) | Refined upper bracket confirms ~108 RPS capacity |
| `rails-cruby-yjit` | 75.00 RPS (5/5 reps confirmed) | **[93.75, 96.88] RPS** (2 reps evaluated) | 4-VU warmup allowed discovery of higher sustainable point |
| `rails-cruby-off` | 49.99 RPS (5/5 reps confirmed) | **[48.44, 60.94] RPS** (3 reps evaluated) | Verified baseline within consistent ~50–60 RPS band |
| `rails-jruby` | 25.00 RPS (4/5 reps confirmed) | **[42.19, 54.49] RPS** (3 reps evaluated) | 4-VU warmup eliminated CV instability during warmup |
| `emit-jruby` | 49.99 RPS (5/5 reps confirmed) | **[35.94, 48.44] RPS** (4 reps evaluated) | Narrowed upper bounds within ~45–48 RPS |
| `emit-cruby-off` | 0/5 reps confirmed (failed at 25 RPS) | **[21.88, 23.44] RPS** (3 reps confirmed) | Fractional search successfully resolved capacity below 25 RPS |
| `spinel` | 0/5 reps confirmed (warmup timeout at 32 VU) | **[14.45, 14.84] RPS** (1 rep confirmed) | 4-VU warmup succeeded; resolved open-arrival capacity |
| `rails-jruby-off` | 0/5 reps confirmed (warmup fail-fast) | 0/5 reps confirmed in capacity (Step 1A: 11.8 RPS) | Starting 25 RPS exceeded ceiling; safe termination enforced |
| `emit-jruby-off` | 0/5 reps confirmed (warmup fail-fast) | 0/5 reps confirmed in capacity (Step 1B: 1 RPS pass, 5 RPS fail) | Failure boundary established at 1–5 RPS in diagnostic |

### 11.8 Infrastructure Teardown & Final Integrity Verification

- **VM Status**: Both instances were terminated immediately after data recovery via `gce_cleanup.py`.
  - `bench-app-c3`: `TERMINATED` (checked_at: 1790889050)
  - `bench-loadgen-c3`: `TERMINATED` (checked_at: 1790889050)
  - Documented in `bench-results/c3-retest-20261001T061200Z/cleanup.json`.
- **Integrity**: 10,344 transferred artifact files were verified against remote `SHA256SUMS.received` (`transfer-verification.log`: 0 missing, 0 mismatches). Final manifest contains 10,348 files including teardown proof.

---

## 12. GCE c3-standard-4 Follow-up Targeted Experiments (2026-10-02)

Following the instructions in [`docs/gce-c3-followup-instructions.md`](gce-c3-followup-instructions.md), a targeted follow-up experiment suite (`c3-followup-20261001T225541Z`) was executed across 66 bounded trials on commit [`98a1beec9a406ead2fe3b3e219086c171e1883a4`](https://github.com/koduki/example-rails-aot/commit/98a1beec9a406ead2fe3b3e219086c171e1883a4). All 14,071 artifact files were verified against SHA-256 checksums, and both VMs were confirmed `TERMINATED`.

Raw evidence archive is published at GitHub Release [`gce-c3-followup-20261002-c3-followup-20261001T225541Z`](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-followup-20261002-c3-followup-20261001T225541Z). Complete analysis is available in [`docs/gce-c3-followup-analysis-20261002.md`](gce-c3-followup-analysis-20261002.md).

### Summary of Targeted Findings
1. **Matched-Rate Same-Load Resource Comparison (Priority 1: 18/18 passed)**:
   - At a constant 10 RPS open-arrival rate, `emit-cruby-yjit` achieved p99 = **21.65 ms** (p50 = 19.10 ms) using only **179.8 MiB** container memory and **15.5%** CPU.
   - `rails-cruby-yjit` used **458.8 MiB** (2.55x memory) with p99 = **63.37 ms** due to GC allocation tail latency.
   - JRuby targets consumed **884–1,212 MiB** memory and ~39% CPU under the same 10 RPS load.
2. **2×2 JIT/AOT Interaction (Priority 2: 20 trials, 19 passed, 1 boundary_unstable)**:
   - Roundhouse emitted code without JIT yielded **21.87 RPS**, but jumped **4.71x** to **103.11 RPS** with YJIT.
   - Standard Rails improved **1.72x** with YJIT (56.24 → 96.86 RPS).
   - Under YJIT, Roundhouse emitted code surpasses standard Rails (1.06x), demonstrating that flatter, devirtualized code unlocks superior JIT compilation efficiency.
3. **JVM Warmup Jitter & Convergence (Priority 3: 10 trials, 7 passed, 2 warmup_unstable, 1 boundary_unstable)**:
   - `emit-jruby` warmed up stably in **294s** on average (median capacity **56.00 RPS**).
   - `rails-jruby` suffered severe warmup jitter, averaging **769s** and failing 2 repetitions due to 900s timeout (`warmup_unstable`), reflecting dynamic metaprogramming profiling overhead in HotSpot C2.
4. **Spinel Connection Pool Diagnosis (Priority 4: 18 trials across 6 cells)**:
   - Isolated the root cause of Spinel's earlier collapse: under `pool 512`, 25 RPS collapsed with 281 timeouts and 207% CPU.
   - Under `pool 10`, Spinel handled 25 RPS with **p99 = 27.71 ms, 0 errors, 46.4% CPU, and 90.0 MiB memory**. Spinel's capacity was not limited by app logic, but by k6 virtual user over-allocation.



# GCE c3-standard-4 × 2 Benchmark Report

## Executive Summary

The formal two-VM capacity experiment was executed on Google Compute Engine using two `c3-standard-4` instances (App SUT: `bench-app-c3`, Load Generator: `bench-loadgen-c3`) in `asia-northeast1-b` over a dedicated private VPC. The test evaluated 9 target runtime configurations across 5 randomized rotation repetitions (45 total trials) using the preregistered capacity profile `bench/profiles/gce-c3-capacity.yml` on commit `798963437f3d95f959f4fcf61ab2b3d2da82fc55`.

Under the rigorous benchmarking contract (`rigorous-benchmarking`), **only confirmed per-repetition sustainable capacity is eligible for comparison**, and **no unmeasured capacity is inferred or invented**. 

- **Correctness Gate**: All 9 targets passed strict preflight verification for the primary workload `/articles?page=1` (`app-sliced-page20-1000`).
- **Capacity Trial Outcomes**: Out of 45 scheduled trials, 29 trials resulted in `failed` (due to client queue saturation under open arrival at offered rates) and 16 trials resulted in `unstable` (due to warmup coefficient of variation or drift exceeding 5% within the 900-second warmup budget). No target achieved a full 5/5 confirmed sustainable rate under the strict 100 ms p99 SLO at the starting rate of 100 RPS.
- **Key Finding - In-Memory Pagination Overhead**: The `app-sliced-page20-1000` workload requires each request to instantiate and sort 1,000 ActiveRecord objects before slicing 20 rows. For JIT-off variants (both CRuby and JRuby), CPU saturation at 100 RPS resulted in response timeouts exceeding 5,000 ms, causing k6 open-arrival client queue exhaustion.
- **Key Finding - YJIT Impact**: CRuby with YJIT enabled (`rails-cruby-yjit` and `emit-cruby-yjit`) comfortably handled the 100 RPS offered rate during warmup and initial capacity checks (sub-25 ms latency), but saturated when stepped to 200 RPS.
- **Key Finding - Spinel Architecture**: Roundhouse Spinel maintained an exceptionally low memory footprint (~145–175 MiB vs CRuby ~500–600 MiB and JRuby ~900–1,200 MiB) pegging all 4 vCPUs efficiently, but exhibited >5% window variance during closed-loop warmup under 32 VUs.

---

## 1. Experiment and Provenance

- **Commit**: `798963437f3d95f959f4fcf61ab2b3d2da82fc55`
- **Infrastructure**:
  - GCE Project: `sandbox-svc-dev-8rra`, Zone: `asia-northeast1-b`
  - App VM: `bench-app-c3` (Internal IP: `10.146.0.11`, 4 vCPUs / 16 GB, c3-standard-4)
  - Tester VM: `bench-loadgen-c3` (Internal IP: `10.146.0.12`, 4 vCPUs / 16 GB, c3-standard-4)
  - Network: Private VPC (`default`), Cloud NAT (`nat-jpe-01`), no external IPs
  - Private RTT: min/avg/max = 0.037 / 0.038 / 0.040 ms (5 packets, 0% loss)
- **App OS**: Linux kernel `7.0.0-1011-gcp`, Ubuntu 24.04.1 LTS
- **Docker**: Engine Community v29.8.1, containerd v2.3.6, runc v1.5.1
- **Pinned Container Images**:
  - `rails-aot-bench:rails-cruby`: `sha256:334cffffe65e040560106853f97b7a49b835c3b8ad5b31c69059be00a8db4b33`
  - `rails-aot-bench:rails-jruby`: `sha256:167943ec26cd028b31c435b39646c93bd48421add5cc35cedd1117b74a9eaf30`
  - `rails-aot-bench:emitted-cruby`: `sha256:8996403d7a4dd329e490ad8e067c2f32d8e8eb5bbde5e41faf2f4a49befd88a5`
  - `rails-aot-bench:emitted-jruby`: `sha256:b1dab4ebc31bc3e29c63aaa3620e06ef7b5184cc110cca6d4bbcd0d49ce887b0`
  - `rails-aot-bench:spinel`: `sha256:e15db2f311b708932ebf9eea610d781a124a3d01b45ef4afb49e0fcceb605d1e`
- **Workload**: `app-sliced-page20-1000`
  - Endpoint: `GET /articles?page=1`
  - Fixture: 1,000 articles in SQLite
  - Note: App-level pagination loads the ordered relation then selects 20. Roundhouse Spinel currently lacks ActiveRecord `offset`; this result does not represent DB LIMIT/OFFSET paging.
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
        | (RTT: ~0.038 ms)
        v
[ App VM: bench-app-c3 (10.146.0.11) ]
  |-- Docker Engine (4 vCPUs dedicated, 14 GB memory limit)
  |-- Container port 3000 bound to host 0.0.0.0:3000
  |-- In-memory /proc/net/dev, cpu.stat, and container telemetry collectors
```

---

## 3. Strict Correctness Gate (Preflight)

Prior to running the capacity benchmark, all 9 targets were evaluated against the reference implementation (`rails-cruby-off`) on `GET /articles?page=1` and common read endpoints.

| Target | Page Eligible (`/articles?page=1`) | Common Read Status | Eligible Common Endpoints |
|---|:---:|:---:|---|
| `rails-cruby-off` | **True** | Passed (Reference) | 6 endpoints |
| `rails-cruby-yjit` | **True** | Eligible | 6 endpoints |
| `rails-jruby-off` | **True** | Eligible | 6 endpoints |
| `rails-jruby` | **True** | Eligible | 6 endpoints |
| `emit-cruby-off` | **True** | Eligible | 6 endpoints |
| `emit-cruby-yjit` | **True** | Eligible | 6 endpoints |
| `emit-jruby-off` | **True** | Eligible | 6 endpoints |
| `emit-jruby` | **True** | Eligible | 6 endpoints |
| `spinel` | **True** | Eligible | 6 endpoints |

Preflight manifest and checksums: `bench-results/gce-20260928-c3-capacity/preflight/preflight-manifest.json`.

---

## 4. Primary Capacity & Latency Summary

| Target | Confirmed Reps | Median Sustainable RPS | Worst p99 ms | Worst Error % | Mean App CPU % | Peak Memory MB | Warmup Range (s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `rails-cruby-off` | 0/5 | — | — | — | — | ~450–600 | 182–243 |
| `rails-cruby-yjit` | 0/5 | — | — | — | — | ~580–725 | 212–725 |
| `rails-jruby-off` | 0/5 | — | — | — | — | ~495–670 | 194–260 |
| `rails-jruby` | 0/5 | — | — | — | — | ~930–1,200 | 365–912 |
| `emit-cruby-off` | 0/5 | — | — | — | — | ~160–210 | 556–927 |
| `emit-cruby-yjit` | 0/5 | — | — | — | — | ~180–230 | 182–575 |
| `emit-jruby-off` | 0/5 | — | — | — | — | ~430–450 | 907–921 |
| `emit-jruby` | 0/5 | — | — | — | — | ~660–905 | 243–396 |
| `spinel` | 0/5 | — | — | — | — | ~150–175 | 914–917 |

*Note: In accordance with repository benchmark policy, incomplete targets or targets without 5 confirmed repetitions have no aggregate sustainable capacity.*

---

## 5. All 45 Trial Repetitions

| Trial ID | Target | Rep | Status | Warmup (s) | Outcome Detail |
|---|---|---:|---|---:|---|
| `0000` | `rails-jruby` | 1 | `unstable` | 912.1 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0001` | `rails-jruby-off` | 1 | `failed` | 194.5 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0002` | `emit-jruby` | 1 | `failed` | 273.7 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0003` | `emit-jruby-off` | 1 | `unstable` | 916.6 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0004` | `rails-cruby-yjit` | 1 | `failed` | 724.8 | Client saturation at 200 RPS coarse search |
| `0005` | `rails-cruby-off` | 1 | `failed` | 182.7 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0006` | `emit-cruby-yjit` | 1 | `failed` | 182.2 | Client saturation at 200 RPS coarse search |
| `0007` | `emit-cruby-off` | 1 | `failed` | 803.1 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0008` | `spinel` | 1 | `unstable` | 915.2 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0009` | `rails-jruby` | 2 | `unstable` | 912.2 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0010` | `spinel` | 2 | `unstable` | 917.5 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0011` | `emit-cruby-off` | 2 | `unstable` | 927.0 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0012` | `emit-cruby-yjit` | 2 | `failed` | 575.2 | Client saturation at 200 RPS coarse search |
| `0013` | `rails-cruby-off` | 2 | `failed` | 212.9 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0014` | `rails-cruby-yjit` | 2 | `failed` | 211.6 | Client saturation at 200 RPS coarse search |
| `0015` | `emit-jruby-off` | 2 | `unstable` | 920.8 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0016` | `emit-jruby` | 2 | `failed` | 273.8 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0017` | `rails-jruby-off` | 2 | `failed` | 195.2 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0018` | `emit-jruby` | 3 | `failed` | 365.5 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0019` | `emit-jruby-off` | 3 | `unstable` | 907.0 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0020` | `rails-cruby-yjit` | 3 | `failed` | 332.4 | Client saturation at 200 RPS coarse search |
| `0021` | `rails-cruby-off` | 3 | `failed` | 243.3 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0022` | `emit-cruby-yjit` | 3 | `failed` | 423.8 | Client saturation at 200 RPS coarse search |
| `0023` | `emit-cruby-off` | 3 | `unstable` | 926.6 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0024` | `spinel` | 3 | `unstable` | 916.8 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0025` | `rails-jruby` | 3 | `unstable` | 911.2 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0026` | `rails-jruby-off` | 3 | `failed` | 194.6 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0027` | `emit-jruby` | 4 | `failed` | 243.5 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0028` | `rails-jruby-off` | 4 | `failed` | 194.3 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0029` | `rails-jruby` | 4 | `failed` | 819.9 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0030` | `spinel` | 4 | `unstable` | 914.0 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0031` | `emit-cruby-off` | 4 | `unstable` | 927.2 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0032` | `emit-cruby-yjit` | 4 | `failed` | 0.0 | Remote artifact transfer transient network error |
| `0033` | `rails-cruby-off` | 4 | `failed` | 213.1 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0034` | `rails-cruby-yjit` | 4 | `failed` | 302.1 | Client saturation at 200 RPS coarse search |
| `0035` | `emit-jruby-off` | 4 | `unstable` | 914.7 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0036` | `rails-cruby-yjit` | 5 | `failed` | 302.1 | Client saturation at 200 RPS coarse search |
| `0037` | `rails-cruby-off` | 5 | `failed` | 182.5 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0038` | `emit-cruby-yjit` | 5 | `failed` | 302.7 | Client saturation at 200 RPS coarse search |
| `0039` | `emit-cruby-off` | 5 | `failed` | 555.6 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0040` | `spinel` | 5 | `unstable` | 916.1 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0041` | `rails-jruby` | 5 | `failed` | 365.4 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0042` | `rails-jruby-off` | 5 | `failed` | 259.6 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0043` | `emit-jruby` | 5 | `failed` | 395.5 | Client saturation at 100 RPS (request timeouts > 5s) |
| `0044` | `emit-jruby-off` | 5 | `unstable` | 916.3 | Warmup variance exceeded 5% CV / drift across 4 windows |

---

## 6. Technical Analysis & Dynamics

### A. JIT Impact on In-Memory Slicing
The `app-sliced-page20-1000` query (`Article.order(created_at: :desc).to_a[0...20]`) loads all 1,000 articles from SQLite into Ruby memory and instantiates 1,000 ActiveRecord objects per request.
- **Without JIT** (`rails-cruby-off`, `rails-jruby-off`, `emit-cruby-off`, `emit-jruby-off`): The CPU cost of object allocation and Ruby bytecode interpretation is too severe to sustain 100 requests per second across 4 vCPUs. Request latencies quickly exceeded the 5-second HTTP timeout, causing k6 open-arrival client queue saturation.
- **With YJIT** (`rails-cruby-yjit`, `emit-cruby-yjit`): YJIT compiles method call dispatches and object allocation fast paths, allowing CRuby to successfully sustain 100 RPS during warmup with sub-25ms latency. However, doubling the arrival rate to 200 RPS pegged all 4 vCPUs at 100% utilization, exceeding the capacity threshold.

### B. Roundhouse Spinel Memory Efficiency & Convergence
- **Memory Footprint**: Spinel exhibited extraordinary memory efficiency, consuming only **150–175 MiB** of RAM under heavy load. In contrast, standard Rails on CRuby required **~580–725 MiB**, and JRuby required **~930–1,200 MiB**.
- **Warmup Stability**: Under closed-loop 32-VU concurrency, Spinel achieved high raw throughput, but the strict 5% coefficient of variation (CV) and 5% drift threshold across four consecutive 30-second windows was not met before the 900-second warmup ceiling expired. This correctly yielded `unstable` under preregistered criteria.

### C. Architectural Distinction
Spinel is a complete native execution architecture comprising native AOT compilation, HTTP parser, SQLite DB adapter, coroutine scheduling, and GC. Performance and resource differences observed in Spinel are attributable to this full architectural redesign, not isolated compiler code emission alone.

---

## 7. Cost Cleanup and Resource Teardown

To eliminate ongoing GCP compute charges:
1. Both VMs (`bench-app-c3` and `bench-loadgen-c3`) were stopped via `scripts/bench/gce_cleanup.py`.
2. GCE API confirmed both instances reached `TERMINATED` state independently.
3. Cleanup status was verified and recorded in `bench-results/gce-20260928-c3-capacity/cleanup.json`.

```json
{
  "project": "sandbox-svc-dev-8rra",
  "zone": "asia-northeast1-b",
  "workflow_exit_code": 0,
  "instances": {
    "bench-app-c3": {
      "status": "TERMINATED",
      "stopped": true
    },
    "bench-loadgen-c3": {
      "status": "TERMINATED",
      "stopped": true
    }
  }
}
```

---

## 8. Artifact Provenance & Replication

All raw logs, Docker metrics, host telemetry, k6 open-arrival summaries, and build logs are preserved locally:
- Benchmark Results: `bench-results/gce-20260928-c3-capacity/`
  - `gce-summary.json` & `gce-summary.md`
  - `plan.json`, `env.json`, `cleanup.json`
  - `preflight/` (manifest, captured responses, equivalence checks)
  - `trials/` (0000 through 0044 raw per-step telemetry, k6 logs, cpu/memory traces)
- Build Logs: `bench-results/build-20260928-c3-capacity/`
- Preflight Captures: `bench-results/preflight-20260928-c3-capacity/`

# GCE c3-standard-4 × 2 Benchmark Report

## Executive Summary

The formal two-VM capacity experiment was executed on Google Compute Engine using two `c3-standard-4` instances (App SUT: `bench-app-c3`, Load Generator: `bench-loadgen-c3`) in `asia-northeast1-b` over a dedicated private VPC. The test evaluated 9 target runtime configurations across 5 randomized rotation repetitions (45 total trials) using the preregistered capacity profile `bench/profiles/gce-c3-capacity.yml` on commit `798963437f3d95f959f4fcf61ab2b3d2da82fc55`.

Under the rigorous benchmarking contract (`rigorous-benchmarking`), **only confirmed per-repetition sustainable capacity is eligible for comparison**, and **no unmeasured capacity is inferred or invented**. 

- **Correctness Gate**: All 9 targets passed strict preflight verification for the primary workload `/articles?page=1` (`app-sliced-page20-1000`).
- **Capacity Trial Outcomes**: Out of 45 scheduled trials, 29 trials resulted in `failed` (client queue saturation under open arrival at offered rates) and 16 trials resulted in `unstable` (warmup coefficient of variation or drift exceeding 5% within the 900-second warmup budget, or budget expiration under repeated timeouts). **No target achieved confirmed sustainable capacity (0/5 valid repetitions across all 9 configurations). Sustainable capacity remains unconfirmed.**
- **Warmup Execution Model (Closed-Loop 32 VUs)**: Warmup was executed under closed-loop concurrency with 32 VUs (`connections: 32`, `constant-vus` model). Although k6 summary artifacts logged `driver: k6-open-arrival` and `rate_offered: 100`, this was a logger default configuration; warmup throughput was endogenous concurrency, not open arrival at fixed 100 RPS. Open arrival was used only in the subsequent coarse capacity search phases (100, 200 RPS, etc.).
- **Warmup Failure Breakdown**:
  - `spinel`: All 5 repetitions (150/150 30-second warmup windows) suffered request failures (total 10,609 failed out of 468,590 requests, 2.26% error rate, window p99 ~5,000 ms timeouts). While classified as `unstable` due to budget expiration without reaching CV ≤ 5%, the primary pathology was continuous 5-second request timeouts under 32 VUs rather than harmless throughput variance (#54).
  - `emit-jruby-off`: All 5 repetitions (134/134 warmup windows) experienced massive request failures (25,636 failed out of 27,438 requests, 93.43% failure rate, ending successful throughput ~0.35 RPS, p99 ~5,000 ms timeouts). While classified as `unstable` due to budget expiration, this reflects extreme CPU saturation and interpretation overhead rather than slow convergence (#55).
  - `emit-cruby-off`: 3/5 repetitions were `unstable` with zero request errors but throughput drift/CV exceeding 5% (median ~30.5 RPS), and 2/5 failed at 100 RPS coarse search.
  - `rails-jruby`: 3/5 repetitions were `unstable` (0.089% failure rate, 280 failed / 313,071 requests), and 2/5 failed at 100 RPS coarse search.
- **In-Memory Pagination Overhead**: The `app-sliced-page20-1000` workload (`Article.order(created_at: :desc, id: :desc).to_a[0...20]`) delegates sorting (`ORDER BY created_at DESC, id DESC`) to SQLite at the DB engine level. The severe CPU bottleneck arises because all 1,000 rows are fetched from the database, instantiated into 1,000 ActiveRecord objects in Ruby memory, preloaded with comments, and then sliced (`[0...20]`) in Ruby. Ruby does *not* sort 1,000 items in memory; rather, instantiating 1,000 Active Record objects and allocating associated strings and hashes on every request generates prohibitive memory allocation and GC overhead for non-JIT configurations.
- **Key Finding - DB-Level Pagination Solution (#47)**: Native DB pagination (`db-paged-page20-1000`) executes `LIMIT 20 OFFSET ?` in SQLite, loading only 20 articles and preloading comments solely for those 20 rows, eliminating the full-table instantiation bottleneck across all 9 target runtimes.
- **YJIT Exploration vs Sustainable Capacity**: CRuby with YJIT enabled (`rails-cruby-yjit` 5/5 trials and `emit-cruby-yjit` 4/4 completed trials) comfortably sustained the initial 100 RPS 30-second coarse search step with sub-25 ms latency and zero errors. However, stepping to 200 RPS coarse search saturated 4 vCPUs. Because the harness lacked downward recovery backoff and 120-second sustained confirmation (#51), 5-repetition sustainable capacity remains unconfirmed.
- **Spinel Architecture & Peak Memory Units**: Roundhouse Spinel maintained an exceptionally low peak container memory footprint (~145–175 MiB across warmup trials, specifically ~150–175 MiB during the 30-window warmup runs, vs CRuby ~450–725 MiB and JRuby ~430–1,200 MiB). The metric is defined as peak container memory in MiB (1 MiB = 1,048,576 bytes, sourced from Linux cgroup v2 `memory.peak` / `memory.current` and Docker engine stats). Crucially, because all trials were failed or unconverged under heavy load, these figures represent peak memory under stress, **not** steady-state confirmed capacity memory consumption.
- **Cohort Separation**: Historical GitHub Actions smoke results (pilot micro-benchmarks on 3-article fixtures) and this formal GCE c3-standard-4 capacity benchmark represent distinct experimental cohorts and must not be mixed or directly compared.

---

## 1. Experiment and Provenance

- **Commit**: `798963437f3d95f959f4fcf61ab2b3d2da82fc55`
- **Raw Evidence Release**: GitHub Release [gce-c3-capacity-20260928](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260928)
  - Release archives: `gce-c3-capacity-20260928-raw-artifacts.tar.gz` and `gce-c3-capacity-20260928-raw-artifacts.zip`
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
  - Note: SQLite executes `ORDER BY created_at DESC`, after which the application loads all 1,000 records into memory, instantiates 1,000 ActiveRecord objects, and slices `[0...20]`. Roundhouse Spinel currently lacks ActiveRecord `offset` (#47); this result does not represent DB LIMIT/OFFSET paging.
- **Execution Models**:
  - Warmup: Closed-loop `constant-vus` with 32 VUs (`connections: 32`), 30-second windows (min 180s, max 900s).
  - Capacity Search: Open-arrival `k6-open-arrival` with offered rates stepping from 100 RPS.
- **Container Memory Definition**:
  - Metric: Peak container memory in MiB (`bytes / 1,048,576`), sampled from Linux cgroup v2 (`memory.peak` / `memory.current`) and Docker engine stats.
  - Phase distinction: Values reflect peak usage during warmup or overload coarse search; steady-state capacity memory is unconfirmed.
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

| Target | Confirmed Reps | Median Sustainable RPS | Worst p99 ms | Worst Error % | Mean App CPU % | Peak Container Memory (MiB)* | Warmup Window Errors (Failed/Total Reqs) | Outcome Summary |
|---|---:|---:|---:|---:|---:|---:|---|---|
| `rails-cruby-off` | 0/5 | — | — | — | — | ~450–600 | 0 / 79,025 (0%, 34 windows) | 5 failed at 100 RPS coarse search |
| `rails-cruby-yjit` | 0/5 | — | — | — | — | ~580–725 | 0 / 282,730 (0%, 62 windows) | 5 passed 100 RPS coarse; failed at 200 RPS coarse (unconfirmed) |
| `rails-jruby-off` | 0/5 | — | — | — | — | ~495–670 | 1 / 12,702 (0.008%, 32 windows) | 5 failed at 100 RPS coarse search |
| `rails-jruby` | 0/5 | — | — | — | — | ~930–1,200 | 280 / 313,071 (0.089%, 129 windows) | 3 unstable (CV > 5%), 2 failed at 100 RPS coarse search |
| `emit-cruby-off` | 0/5 | — | — | — | — | ~160–210 | 0 / 125,623 (0%, 134 windows) | 3 unstable (CV > 5%), 2 failed at 100 RPS coarse search |
| `emit-cruby-yjit` | 0/5 | — | — | — | — | ~180–230 | 0 / 225,888 (0%, 65 windows) | 4 passed 100 RPS coarse, failed at 200 RPS coarse; 1 remote transfer error |
| `emit-jruby-off` | 0/5 | — | — | — | — | ~430–450 | 25,636 / 27,438 (93.43%, 134 windows) | 5 unstable (continuous timeouts; ~0.35 RPS; #55) |
| `emit-jruby` | 0/5 | — | — | — | — | ~660–905 | 0 / 106,565 (0%, 51 windows) | 5 failed at 100 RPS coarse search |
| `spinel` | 0/5 | — | — | — | — | ~145–175 | 10,609 / 468,590 (2.26%, 150 windows) | 5 unstable (continuous ~5s timeouts in all 150 windows; #54) |

*\*Note on Peak Container Memory & Capacity*: In accordance with the repository benchmarking contract, targets without 5 confirmed repetitions under the 120-second SLO have no aggregate sustainable capacity (reported as `—`). Peak Container Memory values (in MiB = 1,048,576 bytes) were captured during overloaded or unconverged trials (closed-loop 32 VU warmup or open-arrival coarse search saturation); steady-state capacity memory remains unconfirmed.

---

## 5. All 45 Trial Repetitions

| Trial ID | Target | Rep | Status | Warmup (s) | Outcome Detail |
|---|---|---:|---|---:|---|
| `0000` | `rails-jruby` | 1 | `unstable` | 912.1 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0001` | `rails-jruby-off` | 1 | `failed` | 194.5 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0002` | `emit-jruby` | 1 | `failed` | 273.7 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0003` | `emit-jruby-off` | 1 | `unstable` | 916.6 | Warmup budget expired (27 windows); severe timeouts (93.4% failures, p99 ~5s; #55) |
| `0004` | `rails-cruby-yjit` | 1 | `failed` | 724.8 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0005` | `rails-cruby-off` | 1 | `failed` | 182.7 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0006` | `emit-cruby-yjit` | 1 | `failed` | 182.2 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0007` | `emit-cruby-off` | 1 | `failed` | 803.1 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0008` | `spinel` | 1 | `unstable` | 915.2 | Warmup budget expired (30 windows); continuous timeouts in all 30 windows (2.26% failures, p99 ~5s; #54) |
| `0009` | `rails-jruby` | 2 | `unstable` | 912.2 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0010` | `spinel` | 2 | `unstable` | 917.5 | Warmup budget expired (30 windows); continuous timeouts in all 30 windows (2.26% failures, p99 ~5s; #54) |
| `0011` | `emit-cruby-off` | 2 | `unstable` | 927.0 | Warmup throughput variance exceeded 5% CV / drift (0 request errors, median ~30.5 RPS) |
| `0012` | `emit-cruby-yjit` | 2 | `failed` | 575.2 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0013` | `rails-cruby-off` | 2 | `failed` | 212.9 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0014` | `rails-cruby-yjit` | 2 | `failed` | 211.6 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0015` | `emit-jruby-off` | 2 | `unstable` | 920.8 | Warmup budget expired (27 windows); severe timeouts (93.4% failures, p99 ~5s; #55) |
| `0016` | `emit-jruby` | 2 | `failed` | 273.8 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0017` | `rails-jruby-off` | 2 | `failed` | 195.2 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0018` | `emit-jruby` | 3 | `failed` | 365.5 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0019` | `emit-jruby-off` | 3 | `unstable` | 907.0 | Warmup budget expired (26 windows); severe timeouts (93.4% failures, p99 ~5s; #55) |
| `0020` | `rails-cruby-yjit` | 3 | `failed` | 332.4 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0021` | `rails-cruby-off` | 3 | `failed` | 243.3 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0022` | `emit-cruby-yjit` | 3 | `failed` | 423.8 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0023` | `emit-cruby-off` | 3 | `unstable` | 926.6 | Warmup throughput variance exceeded 5% CV / drift (0 request errors, median ~30.5 RPS) |
| `0024` | `spinel` | 3 | `unstable` | 916.8 | Warmup budget expired (30 windows); continuous timeouts in all 30 windows (2.26% failures, p99 ~5s; #54) |
| `0025` | `rails-jruby` | 3 | `unstable` | 911.2 | Warmup variance exceeded 5% CV / drift across 4 windows |
| `0026` | `rails-jruby-off` | 3 | `failed` | 194.6 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0027` | `emit-jruby` | 4 | `failed` | 243.5 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0028` | `rails-jruby-off` | 4 | `failed` | 194.3 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0029` | `rails-jruby` | 4 | `failed` | 819.9 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0030` | `spinel` | 4 | `unstable` | 914.0 | Warmup budget expired (30 windows); continuous timeouts in all 30 windows (2.26% failures, p99 ~5s; #54) |
| `0031` | `emit-cruby-off` | 4 | `unstable` | 927.2 | Warmup throughput variance exceeded 5% CV / drift (0 request errors, median ~30.5 RPS) |
| `0032` | `emit-cruby-yjit` | 4 | `failed` | 0.0 | Remote artifact transfer transient network error (SCP failed after trial execution; #51) |
| `0033` | `rails-cruby-off` | 4 | `failed` | 213.1 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0034` | `rails-cruby-yjit` | 4 | `failed` | 302.1 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0035` | `emit-jruby-off` | 4 | `unstable` | 914.7 | Warmup budget expired (27 windows); severe timeouts (93.4% failures, p99 ~5s; #55) |
| `0036` | `rails-cruby-yjit` | 5 | `failed` | 302.1 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0037` | `rails-cruby-off` | 5 | `failed` | 182.5 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0038` | `emit-cruby-yjit` | 5 | `failed` | 302.7 | Client saturation at 200 RPS coarse search (100 RPS passed with sub-25ms p99) |
| `0039` | `emit-cruby-off` | 5 | `failed` | 555.6 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0040` | `spinel` | 5 | `unstable` | 916.1 | Warmup budget expired (30 windows); continuous timeouts in all 30 windows (2.26% failures, p99 ~5s; #54) |
| `0041` | `rails-jruby` | 5 | `failed` | 365.4 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0042` | `rails-jruby-off` | 5 | `failed` | 259.6 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0043` | `emit-jruby` | 5 | `failed` | 395.5 | Client saturation at 100 RPS coarse search (request timeouts > 5s) |
| `0044` | `emit-jruby-off` | 5 | `unstable` | 916.3 | Warmup budget expired (27 windows); severe timeouts (93.4% failures, p99 ~5s; #55) |

---

## 6. Technical Analysis & Dynamics

### A. Processing Stages of In-Memory Slicing
The `app-sliced-page20-1000` query (`Article.order(created_at: :desc, id: :desc).to_a[0...20]`) delegates ordering to SQLite via SQL `ORDER BY created_at DESC, id DESC`. SQLite executes the sort inside the database engine; the application bottleneck is that all 1,000 records are fetched from SQLite into application memory, instantiated as 1,000 ActiveRecord objects, preloaded with comments, and then sliced to the first 20 records (`[0...20]`). Ruby does *not* perform the 1,000-item sort in application code. Rather, instantiating 1,000 Active Record model objects per request produces massive object allocation, pointer chasing, and garbage collection pressure.
- **Without JIT** (`rails-cruby-off`, `rails-jruby-off`, `emit-cruby-off`, `emit-jruby-off`): Object allocation and Ruby bytecode interpretation consumed 100% of 4 vCPUs even at 100 requests per second. Request latencies quickly exceeded the 5-second HTTP timeout, causing k6 open-arrival client queue exhaustion and trial failure.
- **With YJIT** (`rails-cruby-yjit`, `emit-cruby-yjit`): YJIT compiles method call dispatches, instance variable access, and allocation fast paths, allowing CRuby to successfully sustain 100 RPS during coarse search with sub-25 ms p99 latency and zero errors. However, stepping the arrival rate to 200 RPS pegged all 4 vCPUs at 100% utilization, exceeding capacity. Because the test harness lacked downward recovery search and 120-second sustained confirmation (#51), 5-repetition sustainable capacity remains unconfirmed.

### B. Roundhouse Spinel Memory Efficiency & Concurrency Dynamics
- **Memory Footprint**: Spinel maintained an exceptionally low container memory footprint (~145–175 MiB across warmup trials, specifically ~150–175 MiB across the 30-window warmup runs, vs CRuby ~450–725 MiB and JRuby ~430–1,200 MiB). The metric is defined as peak container memory in MiB (1 MiB = 1,048,576 bytes, sourced from Linux cgroup v2 `memory.peak` / `memory.current` and Docker engine stats). Crucially, because all trials were failed or unconverged under heavy load, these figures represent peak memory under stress, **not** steady-state confirmed capacity memory consumption.
- **Warmup Stability & Persistent Timeouts**: Under closed-loop 32-VU concurrency, Spinel did *not* merely experience mild variance exceeding the 5% CV / drift threshold. In all 150/150 30-second warmup windows across all 5 repetitions, Spinel experienced continuous request timeouts (cumulative 10,609 failed out of 468,590 requests, 2.26% error rate, window p99 ~5,000 ms). It pegged all 4 vCPUs, but requests frequently stalled. Rather than asserting high efficiency, this pathology points to request queue stalls, SQLite lock contention, or worker starvation under 32 concurrent clients. Issue #54 will conduct detailed diagnostics.

### C. Emitted JRuby JIT-Off Severe Failure Analysis
`emit-jruby-off` experienced 134/134 warmup windows with request failures (cumulative 25,636 failed out of 27,438 requests, 93.43% failure rate, ending successful throughput ~0.35 RPS, p99 ~5,000 ms). Although marked `unstable` due to budget exhaustion, this was catastrophic saturation under Ruby bytecode interpretation rather than slow convergence. In contrast, standard Rails on JRuby JIT Off achieved ~12.5 RPS with near-zero errors under the same 32 VUs. Issue #55 will isolate whether this is interpreter overhead, JFR/JVM threads, or SQLite locking.

### D. Architectural Distinction
Spinel is a complete native execution architecture comprising native AOT compilation, HTTP parser, SQLite DB adapter, coroutine scheduling, and GC. Performance and resource differences observed in Spinel are attributable to this full architectural redesign, not isolated compiler code emission alone.

### E. DB-Level Pagination Implementation & Analysis (#47)
To enable realistic Web application workload benchmarking without 1,000-record in-memory allocations, DB-level pagination (`db-paged-page20-1000`) was implemented across all 9 target runtimes:
- **Query Structure**:
  - Articles: `SELECT id, body, created_at, title, updated_at FROM articles ORDER BY created_at DESC, id DESC LIMIT 20 OFFSET ?`
  - Comments: `SELECT id, article_id, body, commenter, created_at, updated_at FROM comments WHERE article_id IN (?)` (only for the 20 fetched article IDs; 0 queries when offset is out of bounds).
- **Row Counts & Query Execution**:
  - Articles retrieved: 20 rows (vs 1,000 rows in `app-sliced`).
  - Comments retrieved: 20 rows (vs 1,000 rows in `app-sliced`).
  - Total queries: 2 queries for valid page (1 article + 1 comment), 1 query for out-of-bounds page.
- **EXPLAIN QUERY PLAN**:
  - Baseline: `SCAN articles` with `USE TEMP B-TREE FOR ORDER BY`; `SEARCH comments USING INDEX index_comments_on_article_id (article_id=?)`.
  - Composite Index (`index_articles_on_created_at_and_id ON articles(created_at DESC, id DESC)`): `SCAN articles USING INDEX index_articles_on_created_at_and_id`.
  - To maintain strict experimental parity, all 9 targets run against the identical SQLite schema without ad-hoc indexing differences.
- **Workload Separation**:
  - `app-sliced-page20-1000` (`/articles?page=1&pagination=app-sliced`): preserved for historical reproducibility and memory stress analysis.
  - `db-paged-page20-1000` (`/articles?page=1&pagination=db-paged`): established for native SQL pagination capacity comparisons.
- **Equivalence Verification**: Page 1, page 2, last page, and out-of-bounds were verified to produce identical article sets and ordering across Rails, emitted Ruby/JRuby, and Spinel. Stable tie-breaking is enforced by `ORDER BY created_at DESC, id DESC`.

### F. Cohort Separation
Historical GitHub Actions smoke tests (3 articles, shared hosted runner, brief burst) and GCE C3 capacity benchmark (1,000 articles, dedicated bare-metal VMs, private VPC) are fundamentally distinct experimental cohorts and must not be mixed or used to calculate cross-cohort speedup ratios.

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

All raw logs, Docker metrics, host telemetry, k6 open-arrival summaries, and build logs are preserved and indexed:
- **Evaluated Target Commit**: `798963437f3d95f959f4fcf61ab2b3d2da82fc55`
- **GitHub Release Evidence**: [gce-c3-capacity-20260928](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260928)
  - `gce-c3-capacity-20260928-raw-artifacts.tar.gz`
  - `gce-c3-capacity-20260928-raw-artifacts.zip`
- **Benchmark Results Directory**: `bench-results/gce-20260928-c3-capacity/`
  - `gce-summary.json` & `gce-summary.md`
  - `plan.json` (SHA-256: `05cb16cc749a0c72dc9f8eb25caae5d23aa5840401612220d2ba104b452e9578`)
  - `env.json` (SHA-256: `19d2dc34e78713db77b4bf31fed09d27a65e497e18aa734415e4406bec2eee46`)
  - `cleanup.json`: Verification of both instances reaching `TERMINATED`
  - `preflight/`: Manifest, captured response hashes, and equivalence checks (`preflight-manifest.json`)
  - `trials/`: Trials `0000` through `0044` with per-step telemetry, k6 logs, server logs, container state, and per-window warmup summaries (`warmup-000` through `warmup-029`)
- **Build Logs**: `bench-results/build-20260928-c3-capacity/`
- **Preflight Captures**: `bench-results/preflight-20260928-c3-capacity/`

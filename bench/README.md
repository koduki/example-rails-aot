# Rails / Roundhouse / Spinel benchmark

This directory provides a reproducible **pilot harness** for comparing what
Roundhouse changes under CRuby YJIT and JRuby's own JIT, and for comparing the
emitted program with Spinel AOT. It does not publish a performance ranking.
Smoke and quick use the closed-loop pilot driver. Diagnostic and historical full use k6
constant arrival rate. A fixed offered rate compares latency, errors and
resources at that load; capacity ratios require a separate rate sweep.
Use `gce-c3-capacity.yml` for the formal nine-target two-VM sustained capacity experiment; `full.yml` retains historical fixed-rate pilot behavior.

## Targets and compatibility

| Target | Program | Runtime switch | Group |
| --- | --- | --- | --- |
| `rails-cruby-off` / `rails-cruby-yjit` | Rails | YJIT off / on | Main |
| `emit-cruby-off` / `emit-cruby-yjit` | Roundhouse Ruby | YJIT off / on | Main |
| `rails-jruby` / `emit-jruby` | Rails / Roundhouse Ruby | JRuby JIT on | Main |
| `spinel` | Roundhouse Spinel output | AOT | Main |
| `rails-jruby-off` / `emit-jruby-off` | Rails / Roundhouse Ruby | JRuby JIT off | Diagnostic |

The JVM compiler stays enabled in both JRuby modes. Spinel without Roundhouse
is unsupported for this application; no synthetic zero value is reported.
`bench/targets.yml` is the machine-readable matrix. The canonical application
under `blog/` is Rails **8.0.5.1**, with CSRF verification disabled as an
explicit experiment condition. `prepare_app.py` builds a disposable copy
using a shared Rails **8.0.5.1** dependency set for CRuby
**3.4.5** and JRuby **10.0.7.0** (Ruby 3.4 compatibility), with the JDBC
SQLite adapter 80.0.pre1. The adapter core constrains Active Record to the 8.0
series. The derived copy changes the dependency and runtime configuration,
but no longer rewrites the source Rails version. The Ruby dependencies and their checksums are in
`Gemfile.lock` and `emitted.Gemfile.lock`; the Roundhouse and Spinel versions
remain pinned in the repository toolchain. Container base tags live in
`toolchain.env`; each build records the resolved immutable base image digests,
result image IDs, tool versions, source state, and profile hash in the output.
Retain these manifests with every run; a later tag resolution can differ.

## Run

Requirements: Linux, Docker with build support, Python 3.11+, at least two
allowed logical CPUs, and enough local storage for all five image stages.
The client and application need separate CPU sets. Commands must be run from
the repository root:

```sh
python3 -m unittest discover -s tests/bench -v
python3 scripts/bench/run.py run --dry-run
python3 scripts/bench/run.py build --output bench-results/build-001
python3 scripts/bench/run.py preflight --output bench-results/check-001
python3 scripts/bench/run.py run --profile bench/profiles/quick.yml \
  --app-cpus 0 --load-cpus 1 --output bench-results/pilot-001
python3 scripts/bench/run.py report --output bench-results/pilot-001
```

Use actual CPUs in the process's affinity mask; the above IDs are examples.
Outputs must name fresh directories. `--targets` accepts a comma-separated
subset. `build` resolves base digests and builds all image stages first;
`preflight` starts a fresh SQLite fixture for each target, checks the serving
process's JIT and SQLite state, and compares HTTP responses and persisted
results against Rails on CRuby with YJIT off. `run` expects built images and
repeats preflight, then starts a new container and database for each warmup and
measurement trial. The small fixture contains three articles and three
comments with fixed IDs and timestamps. App and load CPUs cannot overlap.

The quick profile measures small HTML and JSON reads on one app CPU; the full
profile adds additional reads and longer windows. Warmup requires stable
throughput and p95 across windows. Unstable runs, failed requests, timeouts,
and endpoints that fail the preflight are labeled separately; they are never
turned into performance observations. Captures, runtime states, fixture
hashes, container inspection, logs, windows, and per-trial data remain in the
output directory. The offline `report` command summarizes endpoint eligibility
and trial dispositions without producing a performance ranking. Host SMT
siblings and container cgroup cpuset/quota/throttling snapshots are recorded.
A failed/interrupted process returns a nonzero exit status
and records `failure.json`. Containers are removed on exit. GitHub Actions
`.github/workflows/benchmark.yml` builds and runs correctness gates for all
nine targets. Its PR job also runs both short CRUD functional scenarios.
Successful CRUD trials have status `verified` and do not contribute to the
statistical performance summary. The AOT workflow separately checks the
packaged binary and container after each push.

## Correctness gate and interpretation

The gate checks status, media type, relevant HTML DOM, JSON values, redirects,
and database rows after create/update/delete and invalid requests. Only CSRF
secrets, signed stream tokens, nonce values, and redirect response bodies are
normalized. `preflight.json` records the first differing semantic path and
values for each failed comparison. Creation/update timestamps must fall inside
the operation window and unchanged timestamps must stay identical. An HTTP 200
with a JSON fallback is a failure. The runtime probe reads actual YJIT/JRuby compiler state and
per-connection SQLite pragmas from the server process, outside timed paths.

During local CRuby validation, the five read paths matched after repairing the
emitted hidden-field attributes. Known write differences still include JSON
validation error shape. CSRF rejection is deliberately outside this fixture:
the Rails reference does not reject forged writes, so preflight marks that
case `excluded`. `preflight.json` reports the exact status for each target
and case. An endpoint becomes eligible only when its own comparison passes.
A passing read or normal CRUD operation does not imply full application equivalence.
Do not compare throughput from failed or excluded cases.
Reuse of `preflight.json` requires its adjacent `preflight-manifest.json` and
matching source revision, validation code, target matrix, container images,
and result digest. Changed inputs require another preflight.

## Load generation, telemetry, and pairwise reports (P1)

P1 provides open-arrival load generation (`bench/k6/read.js`), continuous
resource telemetry (`scripts/bench/collect.py`), and offline statistical
pairwise comparison reporting (`scripts/bench/report.py`).
For CRUD, one logical operation includes all its HTTP requests: update fetches
the edit form and submits the write; create/delete fetches the form, creates,
and deletes. The report uses successful operations/s and complete operation
p50/p95/p99 and error rate for converged CRUD trials, while raw JSON retains
request-level metrics. The short CI `verified` trials remain functional checks
without published performance figures.

- **k6 Open-Arrival Rate**: Executes `constant-arrival-rate` scenarios against eligible endpoints.
  Tracks started, completed, successful, and dropped iterations. Trials with `dropped_iterations > 0`
  or saturated VUs are automatically labeled as `client_saturated` and excluded from valid capacity rankings.
- **Resource Telemetry**: `collect.py` samples CPU percentage and `docker stats` container memory
  at 1-second intervals, plus cgroup v2 peak memory and CPU throttling. Process RSS is
  unavailable in this collector and is recorded as `null` with a reason.
- **Pairwise Comparison Report**: `report.py` reconstructs summary metrics without re-running servers, outputting
  `summary.json`, `summary.csv`, and `summary.md`. Closed-loop pilot runs report observed
  throughput ratios. Fixed offered rate k6 runs report per-target metrics without
  capacity ratios until a rate sweep establishes an SLO-compliant maximum for each target.
- **Container Execution**: `scripts/run-bench-container.ps1` (PowerShell) and `scripts/run-bench-container.sh` (bash)
  allow running benchmark tests and CLI inside a Linux container to absorb host OS discrepancies.

## GCE two-VM capacity run

See [the preregistered GCE report](../docs/gce-c3-benchmark-report.md) for the
existing VPC/subnet/NAT setup, nine-target commands, SLO, run provenance and
result-review rules. The app VM hosts Docker; only the tester VM hosts k6.
`bench/environments/gce-c3-standard-4.env` is a template: set the actual app
private IP, zone and project before running. A fixed host port 3000 is used
only with `--remote-loadgen`; local CI retains ephemeral ports.

The formal report is generated from per-repetition sustained confirmations
as `gce-summary.md` plus raw `gce-summary.json`. The CI report
`ci-verification-report.md` is a functional gate and contains no capacity
ranking. The historical fixed-rate `full.yml` profile does not estimate
maximum sustainable capacity.

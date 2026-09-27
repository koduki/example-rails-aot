# Rails / Roundhouse / Spinel benchmark (P0)

This directory provides a reproducible **pilot harness** for comparing what
Roundhouse changes under CRuby YJIT and JRuby's own JIT, and for comparing the
emitted program with Spinel AOT. It does not publish a performance ranking.
The current load driver is closed loop; the open-arrival load design and final
statistics belong to #16 and #17. Run the sustained experiment on dedicated
GCE capacity when that work is complete.

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
`bench/targets.yml` is the machine-readable matrix. The application under
`blog/` stays at its existing Rails version. `prepare_app.py` builds a
disposable copy using a shared Rails **8.0.5.1** dependency set for CRuby
**3.4.5** and JRuby **10.0.7.0** (Ruby 3.4 compatibility), with the JDBC
SQLite adapter 80.0.pre1. The Ruby dependencies and their checksums are in
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
`benchmark-p0.yml` builds and runs correctness gates for all nine targets;
the original AOT workflow is unchanged.

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
validation error shape and CSRF rejection; `preflight.json` reports the exact
status for each target and case. An endpoint becomes eligible only when its own
comparison passes. A passing read does not imply full application equivalence.
Do not compare throughput from failed or excluded cases. The current driver
is suitable for checking orchestration, stability handling, and target
configuration; final capacity, latency percentiles, confidence intervals,
and host-cost interpretation require the P1 load/report implementation.

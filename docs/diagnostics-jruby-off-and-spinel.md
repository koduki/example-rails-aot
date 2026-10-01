# C3 runtime failure investigation: JRuby Off and Spinel

This review uses the [2026-09-30 release](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260930), source commit `bf098d3e724af5b84d26da9e8398942716a50009`, and workload `app-sliced-page20-1000`. Earlier claims of confirmed interpreter/SQLite causes and safe operating ranges are withdrawn: the archive does not contain the traces or low-load experiments needed to establish them. The September 28 cohort must be reviewed separately.

## Verified observations

| Target | Failed warmup windows | Failed / total requests | Failure rate | Capacity confirmation |
| --- | ---: | ---: | ---: | --- |
| `emit-jruby-off` | 30/30 | 5,739 / 6,131 | 93.61% | 0/5 |
| `spinel` | 150/150 | 10,929 / 475,935 | 2.296% | 0/5 |

These windows use 32 closed-loop VUs and a five-second client timeout. Historic summaries mislabeled this executor `k6-open-arrival`; the executor and run configuration establish the actual mode. Per-request warning logs contain 5,739 request timeouts for emitted JRuby Off and 10,929 for Spinel, matching their failed-request totals. Historic aggregate failure counts alone cannot distinguish timeouts, network errors, HTTP status errors and response corruption. The diagnostic reader can annotate historic warmup windows from matched per-request timeout logs; unclassified failures stay unknown. The new read summary records categories explicitly; existing files are not rewritten.

Trace each repetition through `trials/per-run.json`, `<trial>/warmup.json`, `warmup-*/k6-summary.json`, `warmup-*/k6.log`, and `<trial>/server.log`. Spinel's server log contains startup messages, not SQL/worker stage traces. The release contains no VU sweep, low-rate confirmation or JFR evidence proving a root cause or safe range.

CPU near four vCPUs is consistent with substantial CPU demand. User-mode CPU does not identify a specific interpreter stack or exclude waits. Compute cgroup deltas inside one trial with aligned timestamps; never subtract counters from different containers. HTTP minimum latency includes HTTP/DB/scheduling effects and is not a measured single-request service time. `compile.mode=OFF` disables JRuby bytecode compilation, not HotSpot by itself; verify actual JVM flags and compiler evidence.

## Discriminating experiments

Use [the measurement protocol](../.agents/skills/gce-benchmark-runbook/references/measurement-protocol.md) and `scripts/bench/load_sweep.py`. Prepare uninstrumented closed-loop cells at 1/2/4/8/16/32 VUs, then separate open-arrival cells at 1/5/10/25/50/100 RPS. Each cell runs three repetitions, a one-VU warmup and a 120-second measurement in a fresh container. Failures and warmup instability remain visible. No capacity ranking follows from this diagnostic sweep.

Keep instrumented cohorts separate. Capture generated-code identity, SQL duration, materialization/preload/render timings, allocation/GC deltas, HTTP connection/worker waits and JVM/CPU traces as appropriate. Test CPU/allocations, DB/connection waits, scheduling/GC and HTTP handling as competing hypotheses. A low/high/low recovery experiment must use one container and record outstanding work; independent sweep cells cannot prove queue drainage.

The current capacity recovery probe records low-load health only and explicitly leaves `server_queue_drained` unknown. If health does not recover within the bounded attempts, the trial fails and retains evidence. Fix runtime code after a trace identifies the failing stage, then repeat the same workload and placement with fresh preflight and images.

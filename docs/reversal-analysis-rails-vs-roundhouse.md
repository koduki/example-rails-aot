# Rails vs Roundhouse: C3 throughput and latency observations

Source: [2026-09-30 C3 raw release](https://github.com/koduki/example-rails-aot/releases/tag/gce-c3-capacity-20260930), measured commit `bf098d3e724af5b84d26da9e8398942716a50009`. Claims previously presented as measured per-stage timings, a confirmed association-preload bottleneck, or completed DB-paging remediation are withdrawn. This archive contains no corresponding microbenchmark source/results or stage traces.

## Closed-loop warmup

Across five repetitions, the median of each trial's final four warmup windows gives:

| Target | Median successful RPS |
| --- | ---: |
| `rails-cruby-off` | 76.82 |
| `emit-cruby-off` | 30.79 |
| `rails-cruby-yjit` | 146.71 |
| `emit-cruby-yjit` | 116.33 |

These are 32-VU diagnostic observations, not maximum sustainable capacity. Do not divide them by hosted Actions results from a three-article workload. The main report's capacity candidates use open arrival, a p99/error SLO and sustained confirmation, so their ordering need not match closed-loop warmup ordering.

## Same offered load

One aligned comparison exists at repetition 3, 100 offered RPS, 120-second confirmation:

| Target | p99 ms | Peak container memory MiB | Mean app CPU % |
| --- | ---: | ---: | ---: |
| `rails-cruby-yjit` | 82.61 | 613.1 | 168.32 |
| `emit-cruby-yjit` | 55.36 | 224.9 | 210.14 |

Find the rep-3 trial in `trials/per-run.json` and its final `confirm-100` summary and `app-telemetry.json`. The app collector includes remote orchestration. This pair supports the lower observed latency/memory of emitted YJIT in that interval; it does not prove an allocation mechanism or a general CPU efficiency gain. More aligned repetitions are needed.

## Pending stage investigation

Inspect generated serving code first. Time SQL, row materialization, association preload, rendering and response writes; record allocation/GC deltas and runtime traces. Publish microbenchmark code, command, runtime/JIT identity, fixture and repeated raw samples. `reversal_analysis.evaluate_stage_costs` describes hypothetical operation counts, not executed code, CPU time or measured scaling. DB paging is a distinct workload and needs its own correctness and repeated C3 run.

Use the [measurement protocol](../.agents/skills/gce-benchmark-runbook/references/measurement-protocol.md). Regenerate observational tables with `scripts/bench/reversal_analysis.py --results-dir PATH --output-md OUTPUT`; it now lists supplied observations and keeps missing evidence explicit.

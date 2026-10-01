# Formal GCE report writing

Use `bench-results/gce-RUN_ID/gce-summary.json` and the raw directory as the source of numbers. The generated `gce-summary.md` is a starting table, not evidence that every target passed. Only publish measured data; the September 30 release is a legacy candidate cohort with unresolved capacity intervals. New runner changes require a separate real GCE run.

## Before drawing conclusions

- Match `env.json` commit, `plan.json` profile, image IDs, app/tester hardware and zone to the intended two-VM topology. Confirm the tester is separate, both have no external IP, and no hosted Actions run is mixed into the GCE dataset.
- Verify the named `app-sliced-page20-1000` workload: 1000 rows in each fixture, page 1 returns 20 ordered articles, and all nine targets pass `preflight/preflight.json`. This is application slicing after reading the relation, **not** DB LIMIT/OFFSET. Keep the Roundhouse/Spinel `offset` blocker explicit.
- Check `trials/per-run.json` for five valid repetitions per target. Each must pass sustained confirmation at the reported `capacity_rps`, p99 ≤ 100 ms, error rate < 0.1%, zero dropped iterations, app samples available, no positive CPU throttling delta, and no tester saturation or network errors. Examine each `capacity-search.json`, `telemetry.json`, `k6.log`, `k6-summary.json`, and tester CPU/network/memory files. Record `capacity_interval` lower/upper offered rates, upper duration, width and tolerance. Missing intervals in legacy data are unresolved candidates, never precisely measured maxima. A missing bracket/upper bound or a failed repetition is incomplete.
- Recompute paired same-repetition ratios from valid targets only; show median, range and variability. Inspect the JRuby warmup/tier variation before describing JIT effects. Separate CRuby/YJIT and JRuby comparisons from Spinel's entire native runtime architecture.

## Document structure

1. **Summary:** research question, workload, environment and which comparisons are complete. State inconclusive findings directly.
2. **Method:** exact commit and profile hash, VM type/zone/VPC path, fixed app CPU allocation, k6 tester separation, fixture, ordering, repetition rotation, warmup, bracket search, 120 s confirmation and SLO. Include raw artifact paths.
3. **Correctness:** preflight table for all nine targets, valid CRUD findings only for the workloads actually run, known `offset` compatibility limit.
4. **Results:** per-target confirmed sustainable RPS median and per-repetition values, p50/p95/p99, errors and drops, app CPU/memory, tester headroom and missing data. Graphs may show distributions but must not conceal failed trials. Show a partial median only with its successful/planned count and no complete ranking.
5. **Analysis:** paired Roundhouse/JIT ratios with variation and limits. Attribute Spinel differences to the full Roundhouse + Spinel stack rather than AOT compilation alone. Note SQLite single-writer constraints for write workloads.
6. **Limitations and summary:** what the test supports, what remains blocked, and the next experiment. Do not turn Actions quick/full smoke numbers into capacity rankings.

Keep raw JSON/log files and `cleanup.json` alongside the report. A verified VM stop is a cost-control result, not a performance measurement; stopped persistent disks still incur charges until reviewed and destroyed.

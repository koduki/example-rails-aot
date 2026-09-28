# Why monitor benchmark progress?

Multi-runtime benchmark experiments across Rails, JRuby, and Spinel take considerable time (tens of trials spanning 1–2 hours on dedicated VMs like GCE `c3-standard-4`).

During these runs:
1. Long silent periods make it difficult to determine whether a workload is progressing, hung on an unhandled exception, or failing warmup convergence loops.
2. Manually querying serial console logs or SSHing repeatedly risks typing errors or disrupting CPU pin sets.
3. Estimating completion time (ETA) requires parsing `plan.json` schedule lengths, verifying completed `trial.json` files, and averaging actual warmup and measurement intervals.

## What the Monitor inspects

The monitor runs a lightweight inspection snippet directly on the host or runner VM:
- **Runner process state**: Checks whether `python3 scripts/bench/run.py` is actively scheduled.
- **Active Docker container**: Observes which candidate container (`rails-bench-*`) is serving traffic and whether it is alive.
- **Driver activity**: Verifies whether `scripts/bench/driver.py` is generating load.
- **Outcome validation**: Reads each completed trial directory to verify that warmup converged and status is `passed`.
- **Accurate ETA**: Calculates rolling averages over completed trials rather than assuming idealized minimum trial durations.

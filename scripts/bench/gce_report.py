"""Formal two-VM report generated only from a complete GCE capacity run."""
import json
import statistics
from pathlib import Path

from capacity import paired_ratios

PAIRS = [('emit-cruby-off', 'rails-cruby-off'),
         ('emit-cruby-yjit', 'rails-cruby-yjit'),
         ('emit-jruby-off', 'rails-jruby-off'),
         ('emit-jruby', 'rails-jruby'),
         ('rails-cruby-yjit', 'rails-cruby-off'),
         ('emit-cruby-yjit', 'emit-cruby-off'),
         ('rails-jruby', 'rails-jruby-off'),
         ('emit-jruby', 'emit-jruby-off'),
         ('spinel', 'rails-cruby-off')]


def generate(root):
    root = Path(root)
    plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
    profile = plan['profile']
    if not profile.get('capacity_search'):
        raise ValueError('This report requires a capacity profile')
    rows = json.loads((root / 'trials/per-run.json').read_text(encoding='utf-8'))
    checks = json.loads((root / 'preflight/preflight.json').read_text(encoding='utf-8'))
    env = json.loads((root / 'env.json').read_text(encoding='utf-8'))
    pairs = {f'{a}/{b}': paired_ratios(rows, a, b) for a, b in PAIRS}
    source = {'schema_version': 1, 'commit': env['git_commit'], 'profile': profile,
              'environment': env, 'checks': checks, 'trials': rows, 'paired': pairs}
    (root / 'gce-summary.json').write_text(json.dumps(source, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    lines = ['# GCE c3-standard-4 × 2 Benchmark Report', '',
             '## Executive Summary', '',
             'Only confirmed per-repetition sustainable capacity is eligible for comparison.',
             'Any missing or failed repetition makes a target incomplete. These results represent the full runtime stacks.', '',
             '## Experiment and provenance', '',
             f'- Commit: `{env["git_commit"]}`',
             f'- App/tester: c3-standard-4 each; zone: `{profile.get("gce_zone")}`; private VPC',
             f'- App host: `{profile.get("target_host")}`; tester: `{profile.get("remote_loadgen")}`',
             f'- App OS: `{env.get("app_environment", "unknown")}`',
             f'- Docker: `{env.get("docker_version", "unknown")}`',
             f'- Image IDs: `{env.get("image_ids", {})}`',
             f'- Network RTT: `{env.get("private_rtt", "unknown")}`',
             '- Tester hardware/software probe, app CPU topology and kernel: see `env.json`.',
             f'- Workload: `{profile.get("workload_name", "unnamed")}` at `{profile["endpoints"][0]}`, {profile["fixture_articles"]} articles, page size 20',
             '- App-level pagination loads the ordered relation then selects 20. Roundhouse Spinel currently lacks ActiveRecord `offset`; this result does not represent DB LIMIT/OFFSET paging.',
             f'- SLO: p99 ≤ {profile["slo_p99_ms"]} ms, errors < {profile["max_error_rate"] * 100}%, no drops or client saturation',
             f'- Repetitions: {profile["repetitions"]}; all trials in `trials/per-run.json`', '',
             '## Two-VM topology', '',
             'Tester VM (k6) → private VPC port 3000 → App VM (Docker SUT)', '',
             '## Correctness gate', '',
             '| Target | Page eligible | Preflight |', '| --- | --- | --- |']
    for target in profile['targets']:
        c = checks.get(target, {})
        status = c.get('status') or ('passed' if c.get('complete') else ('eligible' if profile['endpoints'][0] in c.get('eligible_endpoints', []) else 'missing'))
        lines.append(f'| `{target}` | {profile["endpoints"][0] in c.get("eligible_endpoints", [])} | {status} |')
    lines += ['', '## Primary capacity and latency', '',
              '| Target | Confirmed reps | Median sustainable RPS | Worst p99 ms | Worst error % | Mean app CPU % | Peak memory MB | Warmup s range |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for target in profile['targets']:
        trials = [r for r in rows if r['target'] == target]
        good = [r for r in trials if r['status'] == 'passed' and r.get('capacity_rps')]
        complete = len(good) == profile['repetitions'] and len(trials) == profile['repetitions']
        capacities = [r['capacity_rps'] for r in good]
        p99s = [r['measurement']['latency_ms']['p99'] for r in trials if r.get('measurement')]
        errors = [100 * r['measurement']['requests_failed'] / max(1, r['measurement']['requests_total']) for r in trials if r.get('measurement')]
        memory = [r.get('telemetry', {}).get('summary', {}).get('peak_container_memory_bytes') for r in trials]
        memory = [v / 1048576 for v in memory if v is not None]
        cpu = [r.get('telemetry', {}).get('summary', {}).get('mean_cpu_pct') for r in trials]
        cpu = [v for v in cpu if v is not None]
        warmup = [r['warmup_seconds'] for r in trials if 'warmup_seconds' in r]
        fmt = lambda x: f'{x:.2f}' if x is not None else '—'
        lines.append(f'| `{target}` | {len(good)}/{profile["repetitions"]} | {fmt(statistics.median(capacities) if complete else None)} | {fmt(max(p99s) if p99s else None)} | {fmt(max(errors) if errors else None)} | {fmt(statistics.mean(cpu) if cpu else None)} | {fmt(max(memory) if memory else None)} | {f"{min(warmup):.0f}–{max(warmup):.0f}" if warmup else "—"} |')
    lines += ['', '## All repetitions', '',
              '| Target | Rep | Status | Sustained RPS | Offered RPS | Confirm p99 ms | Error % | App CPU % | Memory MB | Warmup s |',
              '| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        m = r.get('measurement', {})
        tele = r.get('telemetry', {}).get('summary', {})
        cpu_pct = tele.get('mean_cpu_pct', '—')
        mem_mb = round(tele['peak_container_memory_bytes'] / 1048576, 2) if tele.get('peak_container_memory_bytes') is not None else '—'
        lines.append(f'| `{r["target"]}` | {r["repetition"]} | {r["status"]} | {r.get("capacity_rps", "—")} | {r.get("offered_rps", "—")} | {m.get("latency_ms", {}).get("p99", "—")} | {round(100*m.get("requests_failed", 0)/max(1,m.get("requests_total",0)), 3) if m else "—"} | {cpu_pct} | {mem_mb} | {round(r.get("warmup_seconds", 0))} |')
    lines += ['', '## Paired capacity ratios', '', '| Numerator / denominator | Median | Min | Max | IQR | Per repetition |', '| --- | ---: | ---: | ---: | ---: | --- |']
    for key, result in pairs.items():
        per_rep = ', '.join(f"{entry['repetition']}: {entry['ratio']:.3f}" for entry in result['pairs'])
        lines.append(f'| `{key}` | {result["median"] if result["median"] is not None else "—"} | {result["min"] if result["min"] is not None else "—"} | {result["max"] if result["max"] is not None else "—"} | {result["iqr"] if result["iqr"] is not None else "—"} | {per_rep} |')
    lines += ['', '## Warmup, CPU and memory', '',
              'Each trial stores warmup windows, per-step Docker telemetry, capacity-search steps, and tester mpstat/network/memory artifacts. Summary app CPU/memory refers to the confirmation interval (including remote orchestration). Inspect per-repetition tier differences for JRuby before drawing a JIT conclusion.', '',
              '## Spinel interpretation and limitations', '',
              'Spinel includes native compilation, HTTP, DB adapter, scheduling and memory management. The measured difference is attributable to the Roundhouse + Spinel execution architecture as a whole, not the AOT compiler alone.',
              'SQLite single-writer contention remains even when CRUD writes use different article IDs. Hosted Actions smoke observations are excluded from this report.', '',
              '## Raw artifact provenance', '',
              '`plan.json`, `env.json`, `preflight/`, `trials/per-run.json`, each trial’s `capacity-search.json`, `telemetry.json`, remote k6 and tester artifacts, and `gce-summary.json` support recalculation.']
    (root / 'gce-summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return source

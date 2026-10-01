#!/usr/bin/env python3
"""Plan low-load diagnostic cells; execution requires --execute on the app host.

Each cell starts a fresh container. Ordered rates therefore test independent
load points, not queue recovery within one container. Formal capacity is separate.
"""
import argparse
import math
import subprocess
import sys
from pathlib import Path

import run

ROOT = Path(__file__).resolve().parents[2]


def plan(base, mode, values, targets=None, instrumented=False):
    if base.get('driver') != 'k6' or base.get('k6_script', 'bench/k6/read.js') != 'bench/k6/read.js':
        raise ValueError('Diagnostic sweeps require the read k6 profile')
    cells = []
    for index, value in enumerate(values):
        if not math.isfinite(value) or value < 0.001 or (mode == 'vus' and int(value) != value):
            raise ValueError('Sweep values must be positive; VUs must be integers')
        p = dict(base, capacity_search=False, diagnostics=instrumented,
                 allow_unstable=True, retain_phase_artifacts=True,
                 warmup_closed_loop=True, warmup_connections=1,
                 measurement_closed_loop=mode == 'vus', repetitions=3,
                 measurement_seconds=120)
        if targets:
            p['targets'] = targets
        if mode == 'vus':
            p['connections'] = int(value)
        else:
            p['offered_rps'] = value
        p['diagnostic_cell'] = {'mode': mode, 'value': value,
                                'instrumented': instrumented, 'fresh_container': True}
        p = run.config(p)
        cells.append({'id': f'{index:03d}-{mode}-{value:g}', 'profile': p})
    return cells


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=ROOT / 'bench/profiles/diagnostic-load-sweep.yml')
    parser.add_argument('--mode', choices=['rates', 'vus'], required=True)
    parser.add_argument('--values', help='Comma-separated ordered values; defaults from profile')
    parser.add_argument('--targets', help='Comma-separated targets')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--instrumented', action='store_true')
    parser.add_argument('--execute', action='store_true', help='Run prepared cells on the app host')
    parser.add_argument('--preflight-file', type=Path)
    parser.add_argument('--remote-loadgen')
    parser.add_argument('--target-host')
    parser.add_argument('--gce-project')
    parser.add_argument('--gce-zone')
    args = parser.parse_args(argv)
    if args.execute and (not args.preflight_file or not all(
            (args.remote_loadgen, args.target_host, args.gce_project, args.gce_zone))):
        parser.error('Execution requires preflight and all four remote-placement flags')
    base = run.config(args.profile)
    values = ([float(v) for v in args.values.split(',')] if args.values else
              base['arrival_sweep_rps' if args.mode == 'rates' else 'concurrency_sweep_vus'])
    cells = plan(base, args.mode, values, args.targets.split(',') if args.targets else None, args.instrumented)
    args.output.mkdir(parents=True, exist_ok=False)
    commands = []
    for cell in cells:
        profile = args.output / f'{cell["id"]}.json'
        run.save(profile, cell['profile'])
        cmd = [sys.executable, str(ROOT / 'scripts/bench/run.py'), 'run',
               '--profile', str(profile.resolve()), '--output', str((args.output / cell['id']).resolve())]
        for flag in ('preflight_file', 'remote_loadgen', 'target_host', 'gce_project', 'gce_zone'):
            if getattr(args, flag):
                cmd += ['--' + flag.replace('_', '-'), str(getattr(args, flag))]
        commands.append({'cell': cell['id'], 'argv': cmd})
    run.save(args.output / 'sweep-plan.json', {'schema_version': 1, 'diagnostic_only': True,
             'fresh_container_per_cell': True, 'commands': commands, 'executed': args.execute})
    outcomes = []
    try:
        if args.execute:
            for cell in commands:
                result = subprocess.run(cell['argv'], check=False)
                outcomes.append({'cell': cell['cell'], 'exit_code': result.returncode})
                run.save(args.output / 'sweep-results.json', outcomes)
    finally:
        if args.execute:
            run.save(args.output / 'sweep-results.json', outcomes)
    print(f'Prepared {len(cells)} diagnostic cells in {args.output}; executed={args.execute}')
    return 1 if any(row['exit_code'] for row in outcomes) else 0


if __name__ == '__main__':
    raise SystemExit(main())

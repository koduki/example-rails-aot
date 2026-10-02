#!/usr/bin/env python3
"""Prepare targeted mechanism profiles; execute only one explicitly selected cell."""
import argparse
from pathlib import Path
import signal
import subprocess
import sys
import time

import retest_plan
import run

EXPERIMENTS = ('scaling-cruby', 'scaling-jruby', 'scaling-spinel',
               'pagination-cruby', 'pagination-jruby', 'pagination-spinel', 'spinel-fds')
TARGETS = {'cruby': retest_plan.MAIN[:4], 'jruby': retest_plan.MAIN[4:], 'spinel': ['spinel']}


def plan(experiment, raised_nofile=8192):
    if experiment not in EXPERIMENTS:
        raise ValueError('Unknown mechanism experiment')
    if type(raised_nofile) is not int or raised_nofile < 1:
        raise ValueError('raised_nofile must be a positive integer')
    base = retest_plan.plan('matched-rate')[0]['profile']
    base.update(preallocated_vus=10, max_vus=4096, total_timeout=14400,
                fd_observations=False)
    cells = []
    if experiment == 'spinel-fds':
        for limit in (None, raised_nofile):
            for pool in (10, 512):
                profile = dict(base, targets=['spinel'], offered_rps=25,
                    total_timeout=3600, socket_observations=True, socket_interval_seconds=2,
                    fd_observations=True, preallocated_vus=pool)
                if limit is not None:
                    profile['container_nofile'] = limit
                cells.append({'id': f'pool{pool}-nofile{limit or "default"}',
                              'profile': run.config(profile)})
        return cells
    kind, runtime = experiment.split('-')
    counts = (3, 20, 1000) if kind == 'scaling' else (1000,)
    routes = ('app-sliced',) if kind == 'scaling' else ('app-sliced', 'db-paged')
    for count in counts:
        for route in routes:
            endpoint = '/articles?page=1' + ('&pagination=db-paged' if route == 'db-paged' else '')
            profile = dict(base, targets=TARGETS[runtime], fixture_articles=count,
                endpoints=[endpoint], workload_name=f'{route}-page20-{count}')
            cells.append({'id': f'n{count}-{route}', 'profile': run.config(profile)})
    return cells


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', choices=EXPERIMENTS, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--raised-nofile', type=int, default=8192)
    parser.add_argument('--execute-cell', help='Execute exactly this cell, using fresh automatic preflight')
    for flag in ('remote-loadgen', 'target-host', 'gce-project', 'gce-zone'):
        parser.add_argument('--' + flag)
    args = parser.parse_args(argv)
    cells = plan(args.experiment, args.raised_nofile)
    required = ('remote_loadgen', 'target_host', 'gce_project', 'gce_zone')
    if args.execute_cell:
        if args.execute_cell not in {c['id'] for c in cells}:
            parser.error('Unknown cell')
        if not all(getattr(args, field) for field in required):
            parser.error('Execution requires all four remote placement flags')
        if args.target_host in ('127.0.0.1', 'localhost', 'APP_PRIVATE_IP') or args.gce_project == 'PROJECT_ID':
            parser.error('Execution requires real remote placement values')
    args.output.mkdir(parents=True, exist_ok=False)
    commands = []
    for cell in cells:
        profile = args.output / (cell['id'] + '.json')
        run.save(profile, cell['profile'])
        # Deliberately omit --preflight-file: each fixture/route gets fresh checks.
        command = [sys.executable, str(run.ROOT / 'scripts/bench/run.py'), 'run',
                   '--profile', str(profile.resolve()), '--output',
                   str((args.output / cell['id']).resolve()), '--app-cpus', '0,1,2,3']
        for field in required:
            if getattr(args, field):
                command += ['--' + field.replace('_', '-'), getattr(args, field)]
        commands.append({'id': cell['id'], 'argv': command,
            'trial_count': len(cell['profile']['targets']) * cell['profile']['repetitions'],
            'budget_seconds': cell['profile']['total_timeout']})
    run.save(args.output / 'mechanism-plan.json', {
        'schema_version': 1, 'experiment': args.experiment, 'formal_capacity': False,
        'executed_cell': args.execute_cell, 'fresh_preflight_per_cell': True,
        'fresh_container_per_trial': True, 'commands': commands})
    if not args.execute_cell:
        print(f'Prepared {len(cells)} cells; executed=False')
        return 0
    selected = next(c for c in commands if c['id'] == args.execute_cell)
    result = {'id': selected['id'], 'status': 'running', 'started_at': time.time()}
    run.save(args.output / 'mechanism-result.json', result)
    try:
        code = retest_plan.execute(selected['argv'], selected['budget_seconds'])
        result.update(status='completed', exit_code=code)
    except subprocess.TimeoutExpired as error:
        code = 124
        result.update(status='budget_exhausted', error=str(error), exit_code=code)
    except BaseException as error:
        result.update(status='interrupted', error=str(error), exit_code=130)
        raise
    finally:
        result['finished_at'] = time.time()
        run.save(args.output / 'mechanism-result.json', result)
    return int(code != 0)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    raise SystemExit(main())

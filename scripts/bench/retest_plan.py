#!/usr/bin/env python3
"""Prepare bounded follow-up experiments. No VM lifecycle or execution by default."""
import argparse
from pathlib import Path
import subprocess
import sys
import signal
import time

import run

MAIN = ['rails-cruby-off', 'rails-cruby-yjit', 'emit-cruby-off', 'emit-cruby-yjit',
        'rails-jruby', 'emit-jruby']
EXPERIMENTS = ('matched-rate', 'capacity-cruby', 'capacity-jruby',
               'spinel-connections', 'trace-cruby', 'trace-jruby')
BUDGETS = {'matched-rate': 21600, 'capacity-cruby': 28800, 'capacity-jruby': 21600,
           'spinel-connections': 14400, 'trace-cruby': 14400, 'trace-jruby': 14400}


def execute(argv, seconds):
    child = subprocess.Popen(argv)
    try:
        return child.wait(timeout=seconds)
    except BaseException:
        child.terminate()
        try:
            child.wait(timeout=120)
        except subprocess.TimeoutExpired:
            child.kill(); child.wait()
        raise


def plan(experiment):
    if experiment not in EXPERIMENTS:
        raise ValueError('Unknown experiment')
    base = run.config(run.ROOT / 'bench/profiles/gce-c3-capacity.yml')
    base.update(diagnostics=False, socket_observations=False, jfr=False,
                retain_phase_artifacts=True, allow_unstable=False,
                preallocated_vus=512, max_vus=4096,
                recovery_health_p99_ms=2000)
    if experiment.startswith('capacity-'):
        base.update(targets=MAIN[:4] if experiment=='capacity-cruby' else MAIN[4:],
                    total_timeout=28800 if experiment=='capacity-cruby' else 21600)
        return [{'id': experiment, 'profile': run.config(base)}]
    base.update(capacity_search=False, repetitions=3, offered_rps=10,
                measurement_closed_loop=False, measurement_slo_required=True)
    if experiment=='spinel-connections':
        cells=[]
        for rate in (10,15,25):
            for pool in (10,512):
                p=dict(base, targets=['spinel'], offered_rps=rate,
                       preallocated_vus=pool, total_timeout=7200,
                       socket_observations=True, socket_interval_seconds=2)
                cells.append({'id': f'spinel-r{rate}-pool{pool}', 'profile':run.config(p)})
        return cells
    targets=MAIN if experiment=='matched-rate' else MAIN[:4] if experiment=='trace-cruby' else MAIN[4:]
    base.update(targets=targets, total_timeout=21600 if experiment=='matched-rate' else 14400,
                diagnostics=experiment.startswith('trace-'), jfr=experiment=='trace-jruby')
    return [{'id':experiment, 'profile':run.config(base)}]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', choices=EXPERIMENTS, required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--preflight-file',type=Path)
    for flag in ('remote-loadgen','target-host','gce-project','gce-zone'):
        parser.add_argument('--'+flag)
    args=parser.parse_args(argv)
    required=('preflight_file','remote_loadgen','target_host','gce_project','gce_zone')
    if args.execute and not all(getattr(args,f) for f in required):
        parser.error('Execution requires preflight and all four remote placement flags')
    cells=plan(args.experiment)
    args.output.mkdir(parents=True,exist_ok=False)
    commands=[]
    for cell in cells:
        profile=args.output/(cell['id']+'.json');run.save(profile,cell['profile'])
        cmd=[sys.executable,str(run.ROOT/'scripts/bench/run.py'),'run','--profile',str(profile.resolve()),
             '--output',str((args.output/cell['id']).resolve()),'--app-cpus','0,1,2,3']
        for flag in required:
            if getattr(args,flag):cmd+=['--'+flag.replace('_','-'),str(getattr(args,flag))]
        commands.append({'id':cell['id'],'argv':cmd,'trial_count':len(cell['profile']['targets'])*cell['profile']['repetitions'],
                         'total_timeout_seconds':cell['profile']['total_timeout']})
    run.save(args.output/'retest-plan.json',{'schema_version':1,'experiment':args.experiment,
        'executed':args.execute,'fresh_container_per_trial':True,
        'formal_capacity':args.experiment.startswith('capacity-'),
        'experiment_budget_seconds':BUDGETS[args.experiment],'commands':commands})
    outcomes=[{'id':c['id'],'status':'not_run','exit_code':None} for c in commands]
    deadline=time.monotonic()+BUDGETS[args.experiment]
    try:
        if args.execute:
            for cell,row in zip(commands,outcomes):
                remaining=deadline-time.monotonic()
                if remaining<=0:break
                row.update(status='running',started_at=time.time())
                run.save(args.output/'retest-results.json',outcomes)
                try:
                    row.update(exit_code=execute(cell['argv'],remaining),status='completed')
                except subprocess.TimeoutExpired:
                    row.update(status='budget_exhausted',exit_code=124)
                    break
                except BaseException as e:
                    row.update(status='interrupted',error=str(e),exit_code=130)
                    raise
                finally:
                    row['finished_at']=time.time()
                    run.save(args.output/'retest-results.json',outcomes)
    finally:
        if args.execute:run.save(args.output/'retest-results.json',outcomes)
    print(f'Prepared {len(cells)} cells; executed={args.execute}')
    return int(args.execute and any(x['status']!='completed' or x['exit_code'] for x in outcomes))


if __name__=='__main__':
    def interrupt(signum, frame):
        raise KeyboardInterrupt('Retest planner interrupted')
    signal.signal(signal.SIGTERM,interrupt)
    raise SystemExit(main())

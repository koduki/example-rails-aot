#!/usr/bin/env python3
"""Portable P0 build / preflight / lifecycle runner. JSON is used as a YAML subset."""
import argparse
import contextlib
import hashlib
import json
import os
import platform
import random
import signal
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

from prepare import prepare
from preflight import HttpClient, READS, capture, check_probe, compare

ROOT = Path(__file__).resolve().parents[2]
TARGETS = json.loads((ROOT / 'bench/targets.yml').read_text())

def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)

def command(args, timeout=300, log=None, check=True):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if log:
        Path(log).write_text(result.stdout)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {result.stdout[-4000:]}')
    return result.stdout.strip()

def config(path):
    p = json.loads(Path(path).read_text())
    for key in ('cpu_count','memory_mb','threads','spinel_workers','repetitions','window_seconds',
                'warmup_min_seconds','warmup_max_seconds','stable_windows','measurement_seconds',
                'connections','request_timeout','ready_timeout','total_timeout'):
        if type(p.get(key)) not in (int, float) or p[key] <= 0:
            raise ValueError(f'Invalid profile setting: {key}')
    for key in ('cpu_count', 'memory_mb', 'threads', 'spinel_workers', 'repetitions', 'stable_windows', 'connections'):
        if type(p[key]) is not int:
            raise ValueError(f'{key} must be an integer')
    if p['stable_windows'] < 3 or p['warmup_min_seconds'] > p['warmup_max_seconds']:
        raise ValueError('Invalid warmup window policy')
    if any(not 0 < p[k] < 1 for k in ('max_cv', 'max_drift')):
        raise ValueError('Invalid stability tolerance')
    if not p['endpoints'] or set(p['endpoints']) - set(READS):
        raise ValueError('Unknown/empty endpoints')
    select(p['targets'])
    return p

def select(names):
    if not names or len(names) != len(set(names)) or set(names) - set(TARGETS['targets']):
        raise ValueError('Unknown, duplicate, or empty target list')
    return names

def schedule(p):
    rng = random.Random(p['seed'])
    base = list(p['targets']); rng.shuffle(base)
    result = []
    for repetition in range(p['repetitions']):
        # Rotations avoid always measuring one runtime first; reversal reduces drift bias.
        shift = repetition % len(base)
        order = base[shift:] + base[:shift]
        if repetition % 2:
            order = list(reversed(order))
        for endpoint in p['endpoints']:
            result.extend({'target': t, 'endpoint': endpoint, 'repetition': repetition + 1} for t in order)
    return result

def allocation(p, app_cpus=None, load_cpus=None):
    allowed = sorted(os.sched_getaffinity(0))
    app = list(map(int, app_cpus.split(','))) if app_cpus else allowed[:p['cpu_count']]
    client = list(map(int, load_cpus.split(','))) if load_cpus else [c for c in allowed if c not in app]
    if len(set(app)) != p['cpu_count'] or not client or set(app) & set(client) or (set(app)|set(client))-set(allowed):
        raise ValueError('Need distinct allowed CPUs for the application and load generator')
    return {'app': app, 'client': client, 'allowed': allowed}

def stable(windows, p):
    n = p['stable_windows']
    if len(windows) < n:
        return False
    rows = windows[-n:]
    if any(r['errors'] or r['rps'] <= 0 or not r['p95_ms'] for r in rows):
        return False
    for key in ('rps', 'p95_ms'):
        values = [r[key] for r in rows]
        avg = statistics.mean(values)
        half = n // 2
        drift = abs(statistics.mean(values[-half:])-statistics.mean(values[:half])) / avg
        if statistics.pstdev(values)/avg > p['max_cv'] or drift > p['max_drift']:
            return False
    return True

def tag(target):
    return 'rails-aot-bench:' + TARGETS['targets'][target]['image']

class Server:
    def __init__(self, target, directory, profile, cpus):
        self.target, self.directory, self.profile, self.cpus = target, Path(directory), profile, cpus
        self.name = 'rails-bench-' + uuid.uuid4().hex[:12]
        self.database = self.directory / 'data/benchmark.sqlite3'
        self.info = None
    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        self.fixture = prepare(self.database)
        t = TARGETS['targets'][self.target]
        args = ['docker', 'run', '-d', '--name', self.name, '--cpuset-cpus', ','.join(map(str,self.cpus['app'])),
                '--memory', str(self.profile['memory_mb'])+'m', '--memory-swap', str(self.profile['memory_mb'])+'m',
                '-p', '127.0.0.1::3000', '--mount', 'type=bind,src='+str(self.database.parent.resolve())+',dst=/data',
                '-e', 'BENCH_JIT='+t['jit'], '-e', 'RAILS_MAX_THREADS='+str(self.profile['threads']),
                '-e', 'SPINEL_WORKERS='+str(self.profile['spinel_workers']), tag(self.target)]
        save(self.directory / 'launch.json', {'argv': args, 'fixture': self.fixture})
        try:
            start = time.monotonic()
            command(args)
            port = command(['docker','port',self.name,'3000/tcp']).rsplit(':',1)[1]
            self.url = 'http://127.0.0.1:'+port
            client = HttpClient(self.url)
            deadline = start + self.profile['ready_timeout']
            last = ''
            while time.monotonic() < deadline:
                if command(['docker','inspect','--format','{{.State.Running}}',self.name]) != 'true':
                    raise RuntimeError('Server exited before readiness')
                try:
                    probe = client.request('GET','/__bench/runtime')
                    self.info = check_probe(probe,t)
                    response = client.request('GET','/articles')
                    if response['status'] == 200:
                        self.info['ready_seconds'] = time.monotonic()-start
                        save(self.directory/'runtime.json',self.info)
                        return self
                    last = f'Articles status {response["status"]}'
                except (OSError, ValueError, KeyError) as e:
                    last = str(e)
                time.sleep(0.5)
            raise RuntimeError('Readiness deadline exceeded: '+last)
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
    def __exit__(self, *_):
        # Always remove the exact container this trial created; preserve its data and logs.
        command(['docker','logs',self.name],log=self.directory/'server.log',check=False)
        raw = command(['docker','inspect',self.name],check=False)
        try:
            inspect = json.loads(raw)[0]
            save(self.directory/'container.json', {'image':inspect['Image'],'state':inspect['State'],
                 'host_config':{k:inspect['HostConfig'].get(k) for k in ('CpusetCpus','Memory','MemorySwap')}})
        except (ValueError,KeyError,IndexError):
            pass
        command(['docker','rm','-f',self.name],check=False)

def build(names, output):
    builds = []
    pinned = {}
    env = {}
    for line in (ROOT/'bench/toolchain.env').read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=',1); env[key] = value
    for key in ('CRUBY','JRUBY','NATIVE'):
        image = env['BENCH_'+key+'_IMAGE']
        command(['docker','pull',image], timeout=900, log=output/(key.lower()+'-pull.log'))
        metadata = json.loads(command(['docker','image','inspect',image]))[0]
        digests = metadata.get('RepoDigests',[])
        if not digests:
            raise RuntimeError('Base image has no immutable repository digest')
        pinned[key+'_IMAGE'] = digests[0]
    save(output/'base-images.json',pinned)
    for stage in dict.fromkeys(TARGETS['targets'][t]['image'] for t in names):
        args = ['docker','build','--progress=plain','-f','Dockerfile.bench','--target',stage,'-t','rails-aot-bench:'+stage]
        for key,value in pinned.items():
            args += ['--build-arg',key+'='+value]
        command(args+['.'],timeout=3600,log=output/(stage+'-build.log'))
        info = json.loads(command(['docker','image','inspect','rails-aot-bench:'+stage]))[0]
        builds.append({'stage':stage,'id':info['Id'],'created':info['Created']})
        save(output/'images.json',builds)
    return builds

def preflight(names, output, p, cpus):
    reference = TARGETS['reference']
    captures, comparisons = {}, {}
    for name in [reference]+[n for n in names if n != reference]:
        try:
            with Server(name,output/name,p,cpus) as server:
                captures[name] = capture(server.url,server.database,TARGETS['targets'][name])
                save(output/name/'capture.json',captures[name])
            if name == reference:
                comparisons[name] = compare(captures[name],captures[name])
            else:
                comparisons[name] = compare(captures[reference],captures[name])
        except (RuntimeError,ValueError,OSError) as e:
            comparisons[name] = {'status':'failed','reason':str(e),'eligible_endpoints':[],'complete':False}
            if name == reference:
                save(output/'preflight.json',comparisons)
                raise RuntimeError('Reference preflight failed: '+str(e)) from e
        save(output/'preflight.json',comparisons)
    return comparisons

def sample(server, endpoint, duration, p, cpus):
    args = [sys.executable,str(ROOT/'scripts/bench/driver.py'),server.url+endpoint,
            '--duration',str(duration),'--connections',str(p['connections']),
            '--timeout',str(p['request_timeout']),'--cpus',','.join(map(str,cpus['client']))]
    return json.loads(command(args,timeout=duration+p['request_timeout']+15))

def trials(p, cpus, output, checks):
    result = []
    deadline = time.monotonic()+p['total_timeout']
    for index,trial in enumerate(schedule(p)):
        row = dict(trial, status='pending')
        result.append(row)
        if trial['endpoint'] not in checks[trial['target']]['eligible_endpoints']:
            row.update(status='excluded',reason='Endpoint failed preflight')
        elif time.monotonic() >= deadline:
            row.update(status='not_run',reason='Total time budget exhausted')
        else:
            directory = output / f'{index:04d}-{trial["target"]}'
            try:
                with Server(trial['target'],directory,p,cpus) as server:
                    windows = []; elapsed = 0.0; ready = False
                    while elapsed < p['warmup_max_seconds'] and time.monotonic() < deadline:
                        window = sample(server,trial['endpoint'],p['window_seconds'],p,cpus)
                        windows.append(window); elapsed += window['elapsed']
                        save(directory/'warmup.json',windows)
                        if elapsed >= p['warmup_min_seconds'] and stable(windows,p):
                            ready = True; break
                    row['warmup_seconds'] = elapsed
                    if not ready:
                        row.update(status='unstable',reason='No stable window within budget')
                    elif deadline-time.monotonic() < p['measurement_seconds']:
                        row.update(status='not_run',reason='Insufficient remaining measurement budget')
                    else:
                        measured = sample(server,trial['endpoint'],p['measurement_seconds'],p,cpus)
                        row.update(status='failed' if measured['errors'] else 'passed',measurement=measured)
                    save(directory/'trial.json',row)
            except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired) as e:
                row.update(status='failed',reason=str(e))
        save(output/'per-run.json',result)
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build','preflight','run'])
    parser.add_argument('--profile',default=str(ROOT/'bench/profiles/quick.yml'))
    parser.add_argument('--targets',help='Comma-separated target IDs; default from profile')
    parser.add_argument('--output',default='bench-results/'+time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6])
    parser.add_argument('--app-cpus'); parser.add_argument('--load-cpus'); parser.add_argument('--dry-run',action='store_true')
    args = parser.parse_args()
    p = config(args.profile)
    if args.targets:
        p['targets'] = select(args.targets.split(','))
    cpus = allocation(p,args.app_cpus,args.load_cpus)
    plan = {'profile':p,'cpus':cpus,'schedule':schedule(p),'action':args.action,
            'reference':TARGETS['reference'],'targets':TARGETS}
    if args.dry_run:
        print(json.dumps(plan,indent=2)); return 0
    output = Path(args.output).resolve(); output.mkdir(parents=True,exist_ok=False)
    save(output/'plan.json',plan)
    save(output/'env.json', {'schema_version':1,'platform':platform.platform(),'python':sys.version,
         'cpuinfo':Path('/proc/cpuinfo').read_text(),'cpus':cpus,
         'git_commit':command(['git','rev-parse','HEAD']), 'git_status':command(['git','status','--porcelain']),
         'profile_sha256':hashlib.sha256(Path(args.profile).read_bytes()).hexdigest(),
         'target_sha256':hashlib.sha256((ROOT/'bench/targets.yml').read_bytes()).hexdigest(),
         'docker_version':command(['docker','version','--format','{{json .}}'])})
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    try:
        if args.action == 'build':
            build(list(dict.fromkeys([TARGETS['reference']]+p['targets'])),output)
        else:
            checks = preflight(p['targets'],output/'preflight',p,cpus)
            if args.action == 'run':
                rows = trials(p,cpus,output/'trials',checks)
                save(output/'summary.json',{'purpose':'P0 orchestration validation; not a capacity ranking',
                    'passed':sum(r['status']=='passed' for r in rows),'total':len(rows)})
                if any(r['status'] not in ('passed','excluded') for r in rows):
                    return 1
            if any(not set(p['endpoints']).issubset(checks[n]['eligible_endpoints']) for n in p['targets']):
                return 1
        return 0
    except (RuntimeError,ValueError,OSError,KeyboardInterrupt,subprocess.TimeoutExpired) as e:
        save(output/'failure.json',{'status':'failed','reason':str(e) or 'interrupted'})
        print(str(e),file=sys.stderr); return 1

if __name__ == '__main__':
    raise SystemExit(main())

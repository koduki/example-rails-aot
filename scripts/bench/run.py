#!/usr/bin/env python3
"""Portable P0 build / preflight / lifecycle runner. JSON is used as a YAML subset."""
import argparse
import contextlib
import hashlib
import json
import os
import platform
import random
import shutil
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
    if type(p.get('fixture_articles', 3)) is not int or p.get('fixture_articles', 3) < 3:
        raise ValueError('fixture_articles must be an integer >= 3 for the preflight paths')
    if p.get('driver') == 'k6' and (type(p.get('offered_rps')) not in (int, float) or p['offered_rps'] <= 0):
        raise ValueError('k6 offered_rps must be positive')
    minimum = len(p['targets']) * len(p['endpoints']) * p['repetitions'] * (
        p['warmup_min_seconds'] + p['measurement_seconds'])
    if p['total_timeout'] < minimum:
        raise ValueError(f'total_timeout {p["total_timeout"]}s cannot fit the minimum {minimum}s of trials')
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
    allowed = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else list(range(os.cpu_count() or 1))
    app = list(map(int, app_cpus.split(','))) if app_cpus else allowed[:p['cpu_count']]
    client = list(map(int, load_cpus.split(','))) if load_cpus else [c for c in allowed if c not in app]
    if len(set(app)) != p['cpu_count'] or not client or set(app) & set(client) or (set(app)|set(client))-set(allowed):
        raise ValueError('Need distinct allowed CPUs for the application and load generator')
    return {'app': app, 'client': client, 'allowed': allowed}

def cpuset(value):
    ids = set()
    for part in value.split(','):
        bounds = part.split('-')
        if len(bounds) == 1: ids.add(int(bounds[0]))
        elif len(bounds) == 2: ids.update(range(int(bounds[0]),int(bounds[1])+1))
        else: raise ValueError('Invalid cgroup cpuset')
    return ids

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
        self.fixture = prepare(self.database, count=self.profile.get('fixture_articles', 3))
        t = TARGETS['targets'][self.target]
        target_host = self.profile.get('target_host', '127.0.0.1')
        bind_host = '0.0.0.0' if target_host not in ('127.0.0.1', 'localhost') else '127.0.0.1'
        args = ['docker', 'run', '-d', '--name', self.name, '--cpuset-cpus', ','.join(map(str,self.cpus['app'])),
                '--memory', str(self.profile['memory_mb'])+'m', '--memory-swap', str(self.profile['memory_mb'])+'m',
                '-p', f'{bind_host}::3000', '--mount', 'type=bind,src='+str(self.database.parent.resolve())+',dst=/data',
                '-e', 'BENCH_JIT='+t['jit'], '-e', 'RAILS_MAX_THREADS='+str(self.profile['threads']),
                '-e', 'BENCH_PUMA_WORKERS='+str(self.profile['cpu_count'] if t['runtime'] == 'cruby' and self.profile['cpu_count'] > 1 else 0),
                '-e', 'SPINEL_WORKERS='+str(self.profile['spinel_workers'])]
        if self.profile.get('diagnostics'):
            args += ['-e', 'BENCH_DIAGNOSTICS=1']
        args += [tag(self.target)]
        save(self.directory / 'launch.json', {'argv': args, 'fixture': self.fixture})
        try:
            start = time.monotonic()
            command(args)
            port = command(['docker','port',self.name,'3000/tcp']).rsplit(':',1)[1]
            self.url = f'http://{target_host}:{port}'
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
                        limits = self.record_cpu('cpu-start.json')
                        effective = limits.get('cpuset.cpus.effective')
                        if isinstance(effective,str) and cpuset(effective) != set(self.cpus['app']):
                            raise RuntimeError('Container effective CPU set differs from requested CPU set')
                        quota = limits.get('cpu.max')
                        if isinstance(quota,str):
                            maximum,period = quota.split()
                            if maximum != 'max' and int(maximum)/int(period) < len(self.cpus['app']):
                                raise RuntimeError('Container CPU quota is lower than requested CPU count')
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
        # Cleanup cannot be skipped by a failed log/inspect call.
        try:
            self.record_cpu('cpu-end.json')
            with contextlib.suppress(OSError, RuntimeError, subprocess.TimeoutExpired):
                command(['docker','logs',self.name],log=self.directory/'server.log',check=False,timeout=30)
            with contextlib.suppress(OSError, RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError):
                inspect = json.loads(command(['docker','inspect',self.name],check=False,timeout=30))[0]
                save(self.directory/'container.json', {'image':inspect['Image'],'state':inspect['State'],
                     'host_config':{k:inspect['HostConfig'].get(k) for k in ('CpusetCpus','Memory','MemorySwap')}})
        finally:
            command(['docker','rm','-f',self.name],timeout=30)

    def record_cpu(self, filename):
        # cgroup v2 exposes throttling and the effective cpuset. Retain an
        # explicit unavailable marker on hosts using a different layout.
        observed = {}
        for path in ('cpu.stat','cpu.max','cpuset.cpus.effective'):
            try:
                observed[path] = command(['docker','exec',self.name,'cat','/sys/fs/cgroup/'+path],
                                         check=True,timeout=10)
            except (RuntimeError,OSError,subprocess.TimeoutExpired) as e:
                observed[path] = {'unavailable':str(e)}
        save(self.directory/filename,observed)
        return observed

    def get_diagnostics(self):
        try:
            client = HttpClient(self.url)
            res = client.request('GET', '/__bench/diagnostics')
            if res.get('status') == 200 and res.get('body'):
                return json.loads(res['body'])
            return {'available': False, 'status': res.get('status')}
        except Exception as e:
            return {'available': False, 'reason': str(e)}

def report(output):
    """Summarize the correctness gate, trial disposition, and generate P1 pairwise report."""
    root = Path(output)
    preflight_file = root / 'preflight/preflight.json'
    checks = json.loads(preflight_file.read_text(encoding='utf-8')) if preflight_file.exists() else {}
    eligible = sorted(set.intersection(*(set(v.get('eligible_endpoints',[])) for v in checks.values()))) if checks else []
    data = {'purpose':'P0 validity and execution status; not a capacity ranking',
            'eligible_common_reads':eligible,
            'preflight':{name:{'eligible_endpoints':value.get('eligible_endpoints',[]),
                'cases':{case:entry['status'] for case,entry in value.get('cases',{}).items()},
                'status':value.get('status','checked')} for name,value in checks.items()}}
    trials_file = root/'trials/per-run.json'
    if trials_file.exists():
        rows = json.loads(trials_file.read_text(encoding='utf-8'))
        data['trial_status_counts'] = {s:sum(row['status']==s for row in rows)
                                        for s in sorted({row['status'] for row in rows})}
        data['invalid_trials'] = [dict(target=row['target'],endpoint=row['endpoint'],
            repetition=row['repetition'],status=row['status'],reason=row.get('reason'))
            for row in rows if row['status'] != 'passed']
    save(root/'report.json',data)

    import report as p1_report
    p1_report.build_report(root)
    return data

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
        args = ['docker','build','--progress=plain','-f','bench/Dockerfile','--target',stage,'-t','rails-aot-bench:'+stage]
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

def sample(server, endpoint, duration, p, cpus, directory=None):
    if p.get('driver') == 'k6':
        output_dir = Path(directory) if directory else ROOT / 'bench-results/tmp'
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / 'k6-summary.json'
        summary_path.unlink(missing_ok=True)
        rate = p.get('offered_rps', 50)
        client_cpus = ','.join(map(str, cpus['client']))
        script_rel = p.get('k6_script', 'bench/k6/read.js')
        is_crud = script_rel == 'bench/k6/crud.js'
        target_url = server.url if is_crud else server.url + endpoint
        env_args = ['-e', f'TARGET_URL={target_url}', '-e', f'DURATION={int(duration)}s',
                    '-e', f'RATE={rate}', '-e', f'TIMEOUT={int(p["request_timeout"])}s']
        if is_crud:
            env_args += ['-e', f'NUM_ARTICLES={p.get("fixture_articles", 3)}',
                         '-e', f'SCENARIO={p.get("crud_scenario", "mix")}']

        has_local_k6 = False
        try:
            has_local_k6 = subprocess.run(['k6', 'version'], capture_output=True, timeout=2).returncode == 0
        except Exception:
            pass

        script_path = ROOT / script_rel

        if has_local_k6:
            k6_args = ['taskset', '-c', client_cpus, 'k6', 'run', str(script_path),
                       *env_args, '-e', f'SUMMARY_PATH={summary_path}']
            command(k6_args, timeout=duration + p['request_timeout'] + 30)
        else:
            k6_args = [
                'docker', 'run', '--rm',
                '--cpuset-cpus', client_cpus,
                '--network', 'host',
                '-v', f'{script_path.resolve()}:/test_script.js:ro',
                '-v', f'{output_dir.resolve()}:/output',
                *env_args,
                '-e', 'SUMMARY_PATH=/output/k6-summary.json',
                'grafana/k6:latest', 'run', '/test_script.js'
            ]
            command(k6_args, timeout=duration + p['request_timeout'] + 60)

        if not summary_path.exists():
            raise RuntimeError(f'k6 produced no normalized summary: {summary_path}')
        measured = json.loads(summary_path.read_text(encoding='utf-8'))
        required = ('elapsed', 'rps', 'p95_ms', 'requests_total', 'requests_successful',
                    'requests_failed', 'iterations_dropped', 'client_saturated')
        if measured.get('driver') not in ('k6-open-arrival', 'k6-crud') or any(k not in measured for k in required):
            raise ValueError(f'Invalid k6 summary schema: {summary_path}')
        if measured['elapsed'] <= 0 or (measured['requests_successful'] > 0 and (
                measured['p95_ms'] <= 0 or measured.get('latency_ms', {}).get('p99', 0) <= 0)):
            raise ValueError(f'Incomplete k6 duration or latency metrics: {summary_path}')
        return measured

    args = [sys.executable, str(ROOT / 'scripts/bench/driver.py'), server.url + endpoint,
            '--duration', str(duration), '--connections', str(p['connections']),
            '--timeout', str(p['request_timeout']), '--cpus', ','.join(map(str, cpus['client']))]
    return json.loads(command(args, timeout=duration + p['request_timeout'] + 15))

def trials(p, cpus, output, checks):
    result = []
    deadline = time.monotonic() + p['total_timeout']
    for index, trial in enumerate(schedule(p)):
        row = dict(trial, status='pending')
        result.append(row)
        if trial['endpoint'] not in checks[trial['target']]['eligible_endpoints']:
            row.update(status='excluded', reason='Endpoint failed preflight')
        elif time.monotonic() >= deadline:
            row.update(status='not_run', reason='Total time budget exhausted')
        else:
            directory = output / f'{index:04d}-{trial["target"]}'
            try:
                with Server(trial['target'], directory, p, cpus) as server:
                    diag_start = server.get_diagnostics() if p.get('diagnostics') else None
                    windows = []; elapsed = 0.0; ready = False
                    while elapsed < p['warmup_max_seconds'] and time.monotonic() < deadline:
                        window = sample(server, trial['endpoint'], p['window_seconds'], p, cpus, directory)
                        windows.append(window); elapsed += window['elapsed']
                        save(directory / 'warmup.json', windows)
                        if elapsed >= p['warmup_min_seconds'] and stable(windows, p):
                            ready = True; break
                    row['warmup_seconds'] = elapsed
                    diag_warmup = server.get_diagnostics() if p.get('diagnostics') else None
                    if not ready and not p.get('allow_unstable'):
                        row.update(status='unstable', reason='No stable window within budget')
                    elif deadline - time.monotonic() < p['measurement_seconds']:
                        row.update(status='not_run', reason='Insufficient remaining measurement budget')
                    else:
                        collector = None
                        telemetry = {'status': 'unavailable', 'reason': 'Collector could not be started'}
                        try:
                            import collect
                            collector = collect.ResourceCollector(server.name, interval=1.0)
                            collector.start()
                        except Exception as e:
                            telemetry = {'status': 'unavailable', 'reason': str(e)}
                        try:
                            measured = sample(server, trial['endpoint'], p['measurement_seconds'], p, cpus, directory)
                        finally:
                            if collector:
                                telemetry = collector.stop()
                        save(directory / 'telemetry.json', telemetry)
                        diag_end = server.get_diagnostics() if p.get('diagnostics') else None
                        diag_data = {'start': diag_start, 'warmup': diag_warmup, 'end': diag_end} if p.get('diagnostics') else None
                        if diag_data:
                            save(directory / 'diagnostics.json', diag_data)
                        succ_reqs = measured.get('requests_successful') if 'requests_successful' in measured else measured.get('successful', 0)
                        total_reqs = measured.get('requests_total') or (succ_reqs + (measured.get('requests_failed') or measured.get('errors', 0))) or 1
                        failed_reqs = measured.get('requests_failed') if 'requests_failed' in measured else measured.get('errors', 0)
                        err_rate = failed_reqs / total_reqs if total_reqs > 0 else 0.0
                        operations = measured.get('operations', {})
                        has_errors = (succ_reqs == 0) or (err_rate > p.get('max_error_rate', 0.05)) or (
                            operations.get('failed', 0) > 0)
                        final_status = 'failed' if has_errors else ('unstable' if not ready else 'passed')
                        row.update(status=final_status, measurement=measured, telemetry=telemetry)
                        if diag_data:
                            row.update(diagnostics=diag_data)
                    save(directory / 'trial.json', row)
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
                row.update(status='failed', reason=str(e))
        save(output / 'per-run.json', result)
    return result

def load_env_file(path):
    """Load key-value environment variables from file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f'Environment file not found: {path}')
    loaded = {}
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            loaded[k.strip()] = v.strip().strip('"\'')
    return loaded

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['build','preflight','run','report'])
    parser.add_argument('--profile',default=str(ROOT/'bench/profiles/quick.yml'),
                        help='Path to benchmark profile YAML/JSON (default: bench/profiles/quick.yml)')
    parser.add_argument('--targets',help='Comma-separated target IDs; default from profile')
    parser.add_argument('--output',default='bench-results/'+time.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6],
                        help='Destination directory for raw benchmark artifacts')
    parser.add_argument('--app-cpus', help='Comma-separated CPU IDs dedicated to application container')
    parser.add_argument('--load-cpus', help='Comma-separated CPU IDs dedicated to load generator')
    parser.add_argument('--memory-mb', type=int, help='Override memory limit in MB for target containers')
    parser.add_argument('--target-host', help='Hostname or IP for load generator connection (default: 127.0.0.1)')
    parser.add_argument('--env-file', help='Path to environment configuration file (e.g. bench/environments/local-single-host.env)')
    parser.add_argument('--dry-run',action='store_true', help='Validate configuration and print execution plan without running')
    parser.add_argument('--preflight-file', help='Path to existing preflight.json to reuse')
    parser.add_argument('--seed', type=int, help='Override random seed for scheduling')
    args = parser.parse_args()

    loaded = load_env_file(args.env_file) if args.env_file else {}
    env_profile = loaded.get('BENCH_PROFILE') or os.environ.get('BENCH_PROFILE')
    env_targets = loaded.get('BENCH_TARGETS') or os.environ.get('BENCH_TARGETS')
    env_output = loaded.get('BENCH_OUTPUT') or os.environ.get('BENCH_OUTPUT')
    env_app_cpus = loaded.get('BENCH_APP_CPUS') or os.environ.get('BENCH_APP_CPUS')
    env_load_cpus = loaded.get('BENCH_LOAD_CPUS') or os.environ.get('BENCH_LOAD_CPUS')
    env_target_host = loaded.get('BENCH_TARGET_HOST') or os.environ.get('BENCH_TARGET_HOST')
    env_memory_mb = loaded.get('BENCH_MEMORY_MB') or os.environ.get('BENCH_MEMORY_MB')
    env_seed = loaded.get('BENCH_SEED') or os.environ.get('BENCH_SEED')

    output_path = args.output
    if args.output.startswith('bench-results/') and env_output:
        output_path = env_output

    if args.action == 'report':
        print(json.dumps(report(output_path),indent=2,ensure_ascii=False)); return 0

    profile_path = args.profile
    if args.profile == str(ROOT/'bench/profiles/quick.yml') and env_profile:
        profile_path = env_profile

    p = config(profile_path)
    targets_val = args.targets or env_targets
    if targets_val:
        p['targets'] = select(targets_val.split(','))
    minimum = len(p['targets']) * len(p['endpoints']) * p['repetitions'] * (
        p['warmup_min_seconds'] + p['measurement_seconds'])
    if p['total_timeout'] < minimum:
        raise ValueError(f'total_timeout {p["total_timeout"]}s cannot fit the minimum {minimum}s of trials')
    mem_val = args.memory_mb or (int(env_memory_mb) if env_memory_mb else None)
    if mem_val:
        p['memory_mb'] = mem_val
    th_val = args.target_host or env_target_host
    if th_val:
        p['target_host'] = th_val
    seed_val = args.seed if args.seed is not None else (int(env_seed) if env_seed is not None else None)
    if seed_val is not None:
        p['seed'] = seed_val

    app_cpus = args.app_cpus or env_app_cpus
    load_cpus = args.load_cpus or env_load_cpus
    cpus = allocation(p, app_cpus, load_cpus)
    plan = {'profile':p,'cpus':cpus,'schedule':schedule(p),'action':args.action,
            'reference':TARGETS['reference'],'targets':TARGETS}
    if args.dry_run:
        print(json.dumps(plan,indent=2)); return 0
    output = Path(output_path).resolve(); output.mkdir(parents=True,exist_ok=False)
    save(output/'plan.json',plan)
    save(output/'env.json', {'schema_version':1,'platform':platform.platform(),'python':sys.version,
         'cpuinfo':Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else '',
         'cpus':cpus,
         'git_commit':command(['git','rev-parse','HEAD']), 'git_status':command(['git','status','--porcelain']),
         'profile_sha256':hashlib.sha256(Path(profile_path).read_bytes()).hexdigest(),
         'target_sha256':hashlib.sha256((ROOT/'bench/targets.yml').read_bytes()).hexdigest(),
         'docker_version':command(['docker','version','--format','{{json .}}'], check=False) if shutil.which('docker') else None,
         'cpu_smt_siblings':{str(c):Path(f'/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list').read_text().strip()
             for c in cpus['app']+cpus['client'] if Path(f'/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list').exists()}})
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    try:
        if args.action == 'build':
            build(list(dict.fromkeys([TARGETS['reference']]+p['targets'])),output)
        else:
            if getattr(args, 'preflight_file', None) and Path(args.preflight_file).exists():
                checks = json.loads(Path(args.preflight_file).read_text(encoding='utf-8'))
                save(output / 'preflight/preflight.json', checks)
            else:
                checks = preflight(p['targets'],output/'preflight',p,cpus)
            if args.action == 'run' and p.get('k6_script') == 'bench/k6/crud.js':
                required = {'create', 'create_invalid', 'update', 'update_invalid',
                            'delete', 'json_create_invalid', 'csrf_invalid'}
                missing = {target: sorted(case for case in required
                    if checks[target].get('cases', {}).get(case, {}).get('status') != 'passed')
                    for target in p['targets']}
                missing = {target: cases for target, cases in missing.items() if cases}
                if missing:
                    raise RuntimeError(f'CRUD preflight requires passing write and CSRF cases: {missing}')
            if args.action == 'run':
                rows = trials(p,cpus,output/'trials',checks)
                save(output/'summary.json',{'purpose':'Benchmark trial execution summary',
                    'passed':sum(r['status']=='passed' for r in rows),'total':len(rows)})
                report(output)
                allowed_statuses = ('passed', 'excluded', 'unstable') if p.get('allow_unstable') else ('passed', 'excluded')
                if any(r['status'] not in allowed_statuses for r in rows):
                    return 1
            if any(not set(p['endpoints']).issubset(checks[n]['eligible_endpoints']) for n in p['targets']):
                return 1
        return 0
    except (Exception, KeyboardInterrupt) as e:
        save(output/'failure.json',{'status':'failed','reason':str(e) or 'interrupted'})
        print(str(e),file=sys.stderr); return 1

if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Portable P0 build / preflight / lifecycle runner. JSON is used as a YAML subset."""
import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
import platform
import random
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path

from prepare import prepare
from loadgen import RemoteLoadGenerator
import capacity
from preflight import HttpClient, READS, capture, check_probe, compare, snapshot

ROOT = Path(__file__).resolve().parents[2]
TARGETS = json.loads((ROOT / 'bench/targets.yml').read_text())
CRUD_CASES = {
    'mix': frozenset(('update',)),
    'update': frozenset(('update',)),
    'create_delete': frozenset(('create', 'delete')),
    'read': frozenset(),
}

def crud_gate(checks, targets, scenario):
    """Admit only the successful operations the selected workload executes.

    Error rendering and invalid CSRF are still captured by preflight, but are
    not operations in this benchmark-only workload. A missing case fails shut.
    """
    required = CRUD_CASES[scenario]
    missing = {}
    for target in targets:
        failed = sorted(case for case in required
                        if checks.get(target, {}).get('cases', {}).get(case, {}).get('status') != 'passed')
        if failed:
            missing[target] = failed
    return missing

def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    temp.replace(path)

def command(args, timeout=300, log=None, check=True):
    result = subprocess.run(args, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if log:
        Path(log).write_text(result.stdout)
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} exited {result.returncode}: {result.stdout[-4000:]}')
    return result.stdout.strip()

def config(path_or_dict):
    p = copy.deepcopy(path_or_dict) if isinstance(path_or_dict, dict) else json.loads(Path(path_or_dict).read_text(encoding='utf-8'))
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
    for key in ('warmup_max_error_rate', 'max_error_rate'):
        if key in p and (type(p[key]) not in (int, float) or not 0 <= p[key] < 1):
            raise ValueError(f'Invalid error rate limit: {key}')
    if not p['endpoints'] or set(p['endpoints']) - set(READS):
        raise ValueError('Unknown/empty endpoints')
    select(p['targets'])
    if type(p.get('fixture_articles', 3)) is not int or p.get('fixture_articles', 3) < 3:
        raise ValueError('fixture_articles must be an integer >= 3 for the preflight paths')
    if p.get('driver') == 'k6' and (type(p.get('offered_rps')) not in (int, float) or p['offered_rps'] <= 0):
        raise ValueError('k6 offered_rps must be positive')
    if p.get('k6_script') == 'bench/k6/crud.js' and p.get('crud_scenario', 'mix') not in CRUD_CASES:
        raise ValueError('Unknown CRUD scenario')
    if 'verification_only' in p and (type(p['verification_only']) is not bool or
            p['verification_only'] and p.get('k6_script') != 'bench/k6/crud.js'):
        raise ValueError('verification_only requires a CRUD k6 profile and a boolean setting')
    if p.get('capacity_search'):
        if p['driver'] != 'k6' or len(p['endpoints']) != 1 or p['repetitions'] < 5:
            raise ValueError('Capacity profile requires k6, one endpoint, and >=5 repetitions')
        for key in ('capacity_start_rps', 'capacity_max_rps', 'capacity_step_seconds', 'capacity_tolerance_rps', 'slo_p99_ms'):
            if p.get(key, 0) <= 0:
                raise ValueError('Invalid capacity setting: ' + key)
    for key in ('capacity_min_rps', 'capacity_tolerance_ratio', 'recovery_probe_seconds',
                'recovery_max_seconds', 'recovery_probe_rps', 'recovery_health_p99_ms'):
        if key in p and (type(p[key]) not in (int, float) or not math.isfinite(p[key]) or p[key] <= 0):
            raise ValueError('Invalid profile setting: ' + key)
    for key in ('warmup_connections', 'capacity_max_steps', 'recovery_max_attempts'):
        if key in p and (type(p[key]) is not int or p[key] < 1):
            raise ValueError('Invalid profile setting: ' + key)
    for key in ('preallocated_vus', 'max_vus'):
        if key in p and (type(p[key]) is not int or p[key] < 1):
            raise ValueError('Invalid profile setting: ' + key)
    if p.get('max_vus', 4096 if p.get('capacity_search') else 50) < p.get(
            'preallocated_vus', 512 if p.get('capacity_search') else 10):
        raise ValueError('max_vus must be >= preallocated_vus')
    for key in ('socket_observations', 'fd_observations', 'jfr', 'measurement_slo_required'):
        if key in p and type(p[key]) is not bool:
            raise ValueError(key + ' must be boolean')
    if 'container_nofile' in p and (type(p['container_nofile']) is not int or p['container_nofile'] < 1):
        raise ValueError('container_nofile must be a positive integer')
    if p.get('fd_observations') and not p.get('socket_observations'):
        raise ValueError('fd_observations requires socket_observations')
    if 'socket_interval_seconds' in p and (type(p['socket_interval_seconds']) not in (int, float)
            or not math.isfinite(p['socket_interval_seconds']) or p['socket_interval_seconds'] < 1):
        raise ValueError('socket_interval_seconds must be finite and >= 1')
    if p.get('jfr') and (not p.get('diagnostics') or any(
            TARGETS['targets'][t]['runtime'] != 'jruby' for t in p['targets'])):
        raise ValueError('jfr requires diagnostics and JRuby-only targets')
    if p.get('capacity_tolerance_ratio', 0) >= 1:
        raise ValueError('capacity_tolerance_ratio must be below 1')
    if p.get('capacity_min_rps', 0) > p.get('capacity_start_rps', float('inf')):
        raise ValueError('capacity_min_rps exceeds start rate')
    if 'target_capacity_start_rps' in p:
        if not isinstance(p['target_capacity_start_rps'], dict) or any(
                not isinstance(v, (int, float)) or v <= 0 for v in p['target_capacity_start_rps'].values()):
            raise ValueError('target_capacity_start_rps must be a mapping of target to positive RPS')
    if 'target_max_cv' in p:
        if not isinstance(p['target_max_cv'], dict) or any(
                not isinstance(v, (int, float)) or not 0 < v < 1 for v in p['target_max_cv'].values()):
            raise ValueError('target_max_cv must be a mapping of target to float in (0, 1)')
    if 'target_max_drift' in p:
        if not isinstance(p['target_max_drift'], dict) or any(
                not isinstance(v, (int, float)) or not 0 < v < 1 for v in p['target_max_drift'].values()):
            raise ValueError('target_max_drift must be a mapping of target to float in (0, 1)')
    if 'capacity_min_rps' in p:
        if not isinstance(p['capacity_min_rps'], (int, float)) or p['capacity_min_rps'] <= 0:
            raise ValueError('capacity_min_rps must be a positive number')
    if 'warmup_fail_fast_windows' in p:
        if not isinstance(p['warmup_fail_fast_windows'], int) or p['warmup_fail_fast_windows'] < 1:
            raise ValueError('warmup_fail_fast_windows must be an integer >= 1')
    if 'warmup_max_latency_ms' in p:
        if not isinstance(p['warmup_max_latency_ms'], (int, float)) or p['warmup_max_latency_ms'] <= 0:
            raise ValueError('warmup_max_latency_ms must be positive')
    if 'warmup_fail_fast_error_rate' in p:
        if not isinstance(p['warmup_fail_fast_error_rate'], (int, float)) or not 0 < p['warmup_fail_fast_error_rate'] <= 1:
            raise ValueError('warmup_fail_fast_error_rate must be in (0, 1]')
    minimum = len(p['targets'] ) * len(p['endpoints']) * p['repetitions'] * (
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

def allocation(p, app_cpus=None, load_cpus=None, remote=False, dry_run=False):
    allowed = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else list(range(os.cpu_count() or 1))
    app = list(map(int, app_cpus.split(','))) if app_cpus else allowed[:p['cpu_count']]
    client = (list(map(int, load_cpus.split(','))) if load_cpus else allowed[:p['cpu_count']]) if remote else (list(map(int, load_cpus.split(','))) if load_cpus else [c for c in allowed if c not in app])
    if len(set(app)) != p['cpu_count'] or not client or (not remote and set(app) & set(client)) or (not dry_run and set(app)-set(allowed)) or (not remote and set(client)-set(allowed)):
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

def warmup_unrecoverable(windows, p):
    """Detect if warmup is irrecoverably degraded and should exit early.
    Returns (is_unrecoverable, reason).
    """
    fail_fast_windows = p.get('warmup_fail_fast_windows', 3)
    if len(windows) < fail_fast_windows:
        return False, None
    recent = windows[-fail_fast_windows:]

    # 1. Persistent client saturation or dropped iterations
    if all(w.get('client_saturated', False) or w.get('iterations_dropped', 0) > 0 for w in recent):
        return True, f'Warmup aborted early: client saturated across {fail_fast_windows} consecutive windows'

    # 2. Persistent high error rate
    fail_fast_error_rate = p.get('warmup_fail_fast_error_rate', 0.2)
    def err_rate(w):
        failed = w.get('requests_failed', w.get('errors', 0))
        total = w.get('requests_total', w.get('successful', 0) + failed)
        return (failed / total) if total > 0 else 0.0

    if all(err_rate(w) > fail_fast_error_rate for w in recent):
        return True, f'Warmup aborted early: error rate exceeded {fail_fast_error_rate:.0%} across {fail_fast_windows} consecutive windows'

    # 3. Persistent latency exceeding threshold
    slo_p99 = p.get('slo_p99_ms')
    max_lat = p.get('warmup_max_latency_ms', (slo_p99 * 10) if slo_p99 else 2000.0)
    def lat_val(w):
        return w.get('p95_ms') or w.get('latency_ms', {}).get('p95', 0) or 0.0

    if all(lat_val(w) > max_lat for w in recent):
        return True, f'Warmup aborted early: latency exceeded {max_lat}ms across {fail_fast_windows} consecutive windows'

    return False, None

def stable(windows, p, target=None):
    n = p['stable_windows']
    if len(windows) < n:
        return False
    rows = windows[-n:]
    if any(r['rps'] <= 0 or not r['p95_ms'] or
           r['errors'] / max(1, r.get('requests_total', r.get('successful', 0) + r['errors'])) >
           p.get('warmup_max_error_rate', 0) for r in rows):
        return False
    max_cv = (p.get('target_max_cv') or {}).get(target, p['max_cv']) if target else p['max_cv']
    max_drift = (p.get('target_max_drift') or {}).get(target, p['max_drift']) if target else p['max_drift']
    for key in (('rps', 'p95_ms', 'p99_ms') if p.get('capacity_search') else ('rps', 'p95_ms')):
        values = [r.get(key) or r.get('latency_ms', {}).get(key.removesuffix('_ms').replace('p99', 'p99')) for r in rows]
        if any(v is None or v <= 0 for v in values):
            return False
        avg = statistics.mean(values)
        half = n // 2
        drift = abs(statistics.mean(values[-half:])-statistics.mean(values[:half])) / avg
        if statistics.pstdev(values)/avg > max_cv or drift > max_drift:
            return False
    return True

def article_sequence(path):
    """AUTOINCREMENT advances for each successful create, even after deletion."""
    db = sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro', uri=True)
    try:
        row = db.execute("SELECT seq FROM sqlite_sequence WHERE name='articles'").fetchone()
        return row[0] if row else 0
    finally:
        db.close()

def verify_crud_state(before, after, sequence_before, sequence_after, measured, scenario, article_count):
    """Check the timed workload really performed its expected database operations."""
    operations = measured.get('operations', {})
    iterations = measured.get('iterations_completed', 0)
    if iterations != operations.get('total') or iterations != operations.get('successful'):
        raise ValueError('CRUD iteration and successful operation counts differ')
    if before['comments'] != after['comments']:
        raise ValueError('CRUD workload unexpectedly changed comments')
    if scenario == 'create_delete':
        if before['articles'] != after['articles']:
            raise ValueError('Create/delete left a changed or undeleted article')
        if sequence_after - sequence_before != iterations:
            raise ValueError('Create/delete count does not match persisted database sequence')
        return {'status': 'passed', 'created_and_deleted': iterations}
    if sequence_after != sequence_before:
        raise ValueError('Read/update workload unexpectedly created an article')
    expected = {}
    for iteration in range(iterations):
        if scenario == 'update' or scenario == 'mix' and iteration % 10 == 0:
            article_id = 1 + (iteration * 31) % article_count
            expected[article_id] = iteration
    if len(before['articles']) != len(after['articles']) or operations.get('writes') != sum(
            scenario == 'update' or scenario == 'mix' and i % 10 == 0 for i in range(iterations)):
        raise ValueError('Read/update operation count or article count differs')
    prior = {row['id']: row for row in before['articles']}
    current = {row['id']: row for row in after['articles']}
    if prior.keys() != current.keys():
        raise ValueError('Read/update changed article identities')
    for article_id, old in prior.items():
        new = current[article_id]
        if article_id in expected:
            iteration = expected[article_id]
            title = f'Article {article_id} (iteration {iteration})'
            body = f'Updated body for article {article_id} at iteration {iteration}. Preserves bounded storage.'
            if (new['title'], new['body']) != (title, body) or any(
                    new[key] != old[key] for key in ('id', 'created_at')):
                raise ValueError(f'Update was not persisted for article {article_id}')
        elif new != old:
            raise ValueError(f'Unexpected mutation of article {article_id}')
    return {'status': 'passed', 'updated_articles': len(expected)}

def warmup_valid(windows):
    return all(w.get('requests_failed', w.get('errors', 0)) == 0 and
               w.get('iterations_dropped', 0) == 0 and not w.get('client_saturated', False) and
               w.get('operations', {}).get('failed', 0) == 0 for w in windows)

def tag(target):
    return 'rails-aot-bench:' + TARGETS['targets'][target]['image']

def throttled_count(cpu_stat):
    if not isinstance(cpu_stat, str):
        return None
    for line in cpu_stat.splitlines():
        if line.startswith('nr_throttled '):
            return int(line.split()[1])
    return None


def docker_port_mapping(profile):
    """Bind a fixed private-reachable port only in two-VM mode."""
    host = '0.0.0.0' if profile.get('remote_loadgen') else '127.0.0.1'
    port = str(profile.get('target_port', 3000)) if profile.get('remote_loadgen') else ''
    return f'{host}:{port}:3000'


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
        args = ['docker', 'run', '-d', '--name', self.name, '--cpuset-cpus', ','.join(map(str,self.cpus['app'])),
                '--memory', str(self.profile['memory_mb'])+'m', '--memory-swap', str(self.profile['memory_mb'])+'m',
                '-p', docker_port_mapping(self.profile), '--mount', 'type=bind,src='+str(self.database.parent.resolve())+',dst=/data',
                '-e', 'BENCH_JIT='+t['jit'], '-e', 'RAILS_MAX_THREADS='+str(self.profile['threads']),
                '-e', 'BENCH_PUMA_WORKERS='+str(self.profile['cpu_count'] if t['runtime'] == 'cruby' and self.profile['cpu_count'] > 1 else 0),
                '-e', 'SPINEL_WORKERS='+str(self.profile['spinel_workers'])]
        if self.profile.get('diagnostics'):
            args += ['-e', 'BENCH_DIAGNOSTICS=1']
        if self.profile.get('jfr'):
            args += ['-e', 'BENCH_JFR=1']
        if 'container_nofile' in self.profile:
            args += ['--ulimit', 'nofile={0}:{0}'.format(self.profile['container_nofile'])]
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
                        if self.profile.get('fd_observations'):
                            from observe import process_snapshot
                            observed_process = process_snapshot(self.name)
                            save(self.directory/'process-start.json', observed_process)
                            if observed_process.get('nofile') is None:
                                raise RuntimeError('Serving process nofile could not be observed')
                            requested = self.profile.get('container_nofile')
                            if requested is not None and observed_process.get('nofile') != {
                                    'soft': str(requested), 'hard': str(requested)}:
                                raise RuntimeError('Serving process nofile differs from requested limit')
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
            if self.profile.get('jfr'):
                evidence = {'status': 'unavailable'}
                try:
                    evidence['command_output'] = command(['docker', 'exec', self.name,
                        'jcmd', '1', 'JFR.dump', 'name=bench', 'filename=/data/bench.jfr'], timeout=30)
                    recording = self.database.parent / 'bench.jfr'
                    evidence.update(status='captured' if recording.is_file() and recording.stat().st_size else 'unavailable',
                                    file='data/bench.jfr', size_bytes=recording.stat().st_size if recording.is_file() else 0)
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
                    evidence['error'] = str(e)
                save(self.directory / 'jfr-capture.json', evidence)
            self.record_cpu('cpu-end.json')
            with contextlib.suppress(OSError, RuntimeError, subprocess.TimeoutExpired):
                command(['docker','logs',self.name],log=self.directory/'server.log',check=False,timeout=30)
            with contextlib.suppress(OSError, RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError):
                inspect = json.loads(command(['docker','inspect',self.name],check=False,timeout=30))[0]
                save(self.directory/'container.json', {'image':inspect['Image'],'state':inspect['State'],
                     'host_config':{k:inspect['HostConfig'].get(k) for k in ('CpusetCpus','Memory','MemorySwap','Ulimits')}})
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
        observed['timestamp'] = time.time()
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
            'crud_gate_policy':'successful operations only; invalid responses remain failed and CSRF rejection is excluded under the benchmark policy',
            'preflight':{name:{'eligible_endpoints':value.get('eligible_endpoints',[]),
                'cases':{case:entry['status'] for case,entry in value.get('cases',{}).items()},
                'status':value.get('status','checked')} for name,value in checks.items()}}
    trials_file = root/'trials/per-run.json'
    if trials_file.exists():
        rows = json.loads(trials_file.read_text(encoding='utf-8'))
        data['trial_status_counts'] = {s:sum(row['status']==s for row in rows)
                                        for s in sorted({row['status'] for row in rows})}
        data['verified_smoke_trials'] = sum(row['status'] == 'verified' for row in rows)
        data['invalid_trials'] = [dict(target=row['target'],endpoint=row['endpoint'],
            repetition=row['repetition'],status=row['status'],reason=row.get('reason'))
            for row in rows if row['status'] not in ('passed', 'verified')]
    save(root/'report.json',data)

    plan_path = root / 'plan.json'
    profile = json.loads(plan_path.read_text())['profile'] if plan_path.exists() else {}
    if profile.get('capacity_search'):
        import gce_report
        gce_report.generate(root)
    elif not os.environ.get('BENCH_CI') and not (profile.get('allow_unstable') and profile.get('repetitions') == 1):
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

def preflight_identity(names):
    """Bind reused checks to the source and container images actually tested."""
    names = sorted(set(names) | {TARGETS['reference']})
    stages = sorted({TARGETS['targets'][name]['image'] for name in names})
    return {
        'commit': command(['git', 'rev-parse', 'HEAD']),
        'targets_sha256': hashlib.sha256((ROOT/'bench/targets.yml').read_bytes()).hexdigest(),
        'preflight_sha256': hashlib.sha256((ROOT/'scripts/bench/preflight.py').read_bytes()).hexdigest(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'image_ids': {stage: json.loads(command(['docker', 'image', 'inspect', 'rails-aot-bench:'+stage]))[0]['Id']
                      for stage in stages},
    }

def save_preflight_manifest(path, names):
    path = Path(path)
    save(path.with_name('preflight-manifest.json'), {
        'schema_version': 1,
        'preflight_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'targets': sorted(set(names) | {TARGETS['reference']}),
        'identity': preflight_identity(names),
    })

def reuse_preflight(path, names):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f'Preflight file does not exist: {path}')
    manifest_path = path.with_name('preflight-manifest.json')
    if not manifest_path.is_file():
        raise ValueError(f'Preflight manifest is missing: {manifest_path}; rerun preflight')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    requested = set(names) | {TARGETS['reference']}
    if manifest.get('schema_version') != 1 or not requested.issubset(set(manifest.get('targets', []))):
        raise ValueError('Preflight target coverage is stale; rerun preflight')
    if manifest.get('preflight_sha256') != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('Preflight results changed since validation; rerun preflight')
    actual = preflight_identity(manifest['targets'])
    if manifest.get('identity') != actual:
        raise ValueError('Preflight source or container images changed; rerun preflight')
    checks = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(checks, dict) or not requested.issubset(checks):
        raise ValueError('Preflight results do not cover requested targets; rerun preflight')
    return checks

def sample(server, endpoint, duration, p, cpus, directory=None, rate=None, phase=None):
    """Retain phase wall time and optional sockets even on sampling failure."""
    output = Path(directory) if directory else ROOT / 'bench-results/tmp'
    output.mkdir(parents=True, exist_ok=True)
    started = time.time(); clock = time.monotonic(); sockets = None
    evidence = {'schema_version': 1, 'phase': phase, 'started_at': started,
                'duration_configured_seconds': duration, 'status': 'failed',
                'diagnostics': bool(p.get('diagnostics')), 'jfr': bool(p.get('jfr')),
                'socket_observations': bool(p.get('socket_observations')),
                'fd_observations': bool(p.get('fd_observations') and phase == 'measurement')}
    try:
        if p.get('socket_observations'):
            from observe import SocketCollector
            options = {'fd_observations': True} if evidence['fd_observations'] else {}
            sockets = SocketCollector(server.name, p.get('socket_interval_seconds', 2), **options)
            sockets.start()
        measured = _sample(server, endpoint, duration, p, cpus, output, rate, phase)
        evidence.update(status='completed', measurement_elapsed_seconds=measured.get('elapsed'))
        return measured
    except BaseException as e:
        evidence['error'] = str(e)
        raise
    finally:
        try:
            if sockets:
                observed = sockets.stop()
                process_fds = observed.pop('process_fds', None)
                save(output / 'socket-observations.json', observed)
                if process_fds is not None:
                    save(output / 'fd-observations.json', process_fds)
        except BaseException as e:
            evidence.update(status='artifact_error', error=str(e))
            raise
        finally:
            evidence.update(finished_at=time.time(), wall_seconds=time.monotonic()-clock)
            save(output / 'phase.json', evidence)


def _sample(server, endpoint, duration, p, cpus, directory=None, rate=None, phase=None):
    if p.get('driver') == 'k6':
        output_dir = Path(directory) if directory else ROOT / 'bench-results/tmp'
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / 'k6-summary.json'
        summary_path.unlink(missing_ok=True)
        rate = rate if rate is not None else p.get('offered_rps', 50)
        client_cpus = ','.join(map(str, cpus['client']))
        script_rel = p.get('k6_script', 'bench/k6/read.js')
        is_crud = script_rel == 'bench/k6/crud.js'
        target_url = server.url if is_crud else server.url + endpoint
        env_args = ['-e', f'TARGET_URL={target_url}', '-e', f'DURATION={int(duration)}s',
                    '-e', f'RATE={rate}', '-e', f'TIMEOUT={int(p["request_timeout"])}s']
        env_args += ['-e', 'PRE_ALLOCATED_VUS=' + str(p.get('preallocated_vus', 512 if p.get('capacity_search') else 10)),
                     '-e', 'MAX_VUS=' + str(p.get('max_vus', 4096 if p.get('capacity_search') else 50))]
        if phase != 'warmup' and p.get('measurement_closed_loop'):
            env_args += ['-e', 'MODE=closed', '-e', f'WARMUP_VUS={p["connections"]}']
        if phase == 'warmup' and p.get('warmup_closed_loop'):
            env_args += ['-e', 'MODE=closed', '-e', f'WARMUP_VUS={p.get("warmup_connections", p.get("connections", 32))}']
        if is_crud:
            env_args += ['-e', f'NUM_ARTICLES={p.get("fixture_articles", 3)}',
                         '-e', f'SCENARIO={p.get("crud_scenario", "mix")}']
        else:
            expected_articles = min(p.get('fixture_articles', 20), 20)
            env_args += ['-e', f'EXPECTED_ARTICLES={expected_articles}']
        env_map = dict(v.split('=', 1) for i, v in enumerate(env_args) if i > 0 and env_args[i-1] == '-e')
        save(output_dir / 'k6-invocation.json', {'script': script_rel, 'environment': env_map,
             'phase': phase, 'load_mode': env_map.get('MODE', 'open'),
             'remote_loadgen': p.get('remote_loadgen')})

        if p.get('remote_loadgen'):
            RemoteLoadGenerator(p['remote_loadgen'], p['gce_zone'], p['gce_project']).run(
                ROOT / script_rel, env_map, output_dir, duration + p['request_timeout'] + 30)
            measured = json.loads(summary_path.read_text())
            # Keep a normalized source file alongside the raw tester artifacts.
            return validate_k6(measured, summary_path, is_crud)
        has_local_k6 = False
        try:
            has_local_k6 = subprocess.run(['k6', 'version'], capture_output=True, timeout=2).returncode == 0
        except Exception:
            pass

        script_path = ROOT / script_rel

        if has_local_k6:
            k6_args = ['taskset', '-c', client_cpus, 'k6', 'run', str(script_path),
                       *env_args, '-e', f'SUMMARY_PATH={summary_path}']
            command(k6_args, timeout=duration + p['request_timeout'] + 30,
                    log=output_dir / 'k6.log')
        else:
            k6_user = f'{os.getuid()}:{os.getgid()}' if hasattr(os, 'getuid') else '1000:1000'
            k6_args = [
                'docker', 'run', '--rm',
                '--user', k6_user,
                '--cpuset-cpus', client_cpus,
                '--network', 'host',
                '-v', f'{script_path.resolve()}:/test_script.js:ro',
                '-v', f'{output_dir.resolve()}:/output',
                *env_args,
                '-e', 'SUMMARY_PATH=/output/k6-summary.json',
                'grafana/k6:latest', 'run', '/test_script.js'
            ]
            command(k6_args, timeout=duration + p['request_timeout'] + 60,
                    log=output_dir / 'k6.log')

        if not summary_path.exists():
            log_path = output_dir / 'k6.log'
            detail = log_path.read_text(encoding='utf-8')[-1200:] if log_path.exists() else 'no k6 log'
            raise RuntimeError(f'k6 produced no normalized summary: {summary_path}\n{detail}')
        measured = json.loads(summary_path.read_text(encoding='utf-8'))
        return validate_k6(measured, summary_path, is_crud)

    args = [sys.executable, str(ROOT / 'scripts/bench/driver.py'), server.url + endpoint,
            '--duration', str(duration), '--connections', str(p['connections']),
            '--timeout', str(p['request_timeout']), '--cpus', ','.join(map(str, cpus['client']))]
    return json.loads(command(args, timeout=duration + p['request_timeout'] + 15))

def validate_k6(measured, summary_path, is_crud):
    required = ('elapsed', 'rps', 'p95_ms', 'requests_total', 'requests_successful',
                'requests_failed', 'iterations_dropped', 'client_saturated')
    if measured.get('driver') not in ('k6-open-arrival', 'k6-closed-loop', 'k6-crud') or any(k not in measured for k in required):
        raise ValueError(f'Invalid k6 summary schema: {summary_path}')
    if measured['elapsed'] <= 0 or (measured['requests_successful'] > 0 and (
            measured['p95_ms'] <= 0 or measured.get('latency_ms', {}).get('p99', 0) <= 0)):
        raise ValueError(f'Incomplete k6 duration or latency metrics: {summary_path}')
    if is_crud and (measured.get('driver') != 'k6-crud' or
            measured.get('operations', {}).get('ops_successful_rate') is None or
            measured.get('operation_latency_ms', {}).get('p99', 0) <= 0):
        raise ValueError(f'Incomplete CRUD operation rate or latency metrics: {summary_path}')
    return measured

def recover(server, endpoint, profile, cpus, directory):
    """Bounded low-load health checks; does not assert server queue drainage."""
    root = Path(directory)
    attempts = []
    health_profile = dict(profile, slo_p99_ms=profile.get('recovery_health_p99_ms', profile['request_timeout'] * 1000))
    recovery_evidence = {'health_p99_ms': health_profile['slo_p99_ms'], 'server_queue_drained': None,
        'limitation': 'Response health is separate from performance SLO. '
                      'No active-request gauge; healthy low-load probes do not prove queue drainage.'}
    deadline = time.monotonic() + profile.get('recovery_max_seconds', 60)
    for index in range(profile.get('recovery_max_attempts', 3)):
        if time.monotonic() >= deadline:
            break
        try:
            m = sample(server, endpoint, profile['recovery_probe_seconds'], profile, cpus,
                       root / f'{index:03d}', rate=profile.get('recovery_probe_rps', 1), phase='recovery')
        except Exception as error:
            attempts.append({'attempt': index, 'decision': 'measurement_error', 'error': str(error)})
            save(root / 'recovery.json', dict(recovery_evidence, status='unrecovered', attempts=attempts))
            raise
        state = capacity.decision(m, health_profile)
        attempts.append({'attempt': index, 'decision': state,
                         'performance_slo_decision': capacity.decision(m, profile), 'measurement': m})
        save(root / 'recovery.json', dict(recovery_evidence,
             status='healthy_probe' if state == 'pass' else 'pending', attempts=attempts))
        if state == 'pass':
            return m
        if state in ('invalid_client', 'transport_error', 'unclassified'):
            break
    save(root / 'recovery.json', dict(recovery_evidence, status='unrecovered', attempts=attempts))
    raise RuntimeError('SUT recovery not verified; restart and rewarm in a separate trial')


def trials(p, cpus, output, checks):
    result = [dict(trial, status='not_run', reason='Trial not started') for trial in schedule(p)]
    save(output / 'per-run.json', result)
    deadline = time.monotonic() + p['total_timeout']
    for index, trial in enumerate(result):
        row = trial
        row.update(status='pending')
        row.pop('reason', None)
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
                    verification_only = p.get('verification_only', False)
                    early_abort_reason = None
                    while elapsed < p['warmup_max_seconds'] and time.monotonic() < deadline:
                        save(directory / 'progress.json', {'target': trial['target'], 'repetition': trial['repetition'], 'phase': 'warmup', 'window': len(windows) + 1})
                        window = sample(server, trial['endpoint'], p['window_seconds'], p, cpus, directory / f'warmup-{len(windows):03d}' if p.get('capacity_search') or p.get('retain_phase_artifacts') else directory, phase='warmup')
                        windows.append(window); elapsed += window['elapsed']
                        save(directory / 'warmup.json', windows)
                        # Functional smoke may see a startup timeout before the
                        # JVM settles. Require a clean *last* window and retain
                        # earlier failures in the artifact for audit.
                        if verification_only and elapsed >= p['warmup_min_seconds'] and warmup_valid(windows[-1:]):
                            break
                        if not verification_only and elapsed >= p['warmup_min_seconds'] and stable(windows, p, target=trial['target']):
                            ready = True; break
                        if not verification_only and elapsed >= p['warmup_min_seconds']:
                            aborted, reason = warmup_unrecoverable(windows, p)
                            if aborted:
                                early_abort_reason = reason
                                break
                    row['warmup_seconds'] = elapsed
                    row['warmup_converged'] = None if verification_only else ready
                    if verification_only:
                        row['warmup_failed_windows'] = sum(not warmup_valid([w]) for w in windows)
                    diag_warmup = server.get_diagnostics() if p.get('diagnostics') else None
                    warmup_clean = warmup_valid(windows[-1:])
                    if verification_only and p.get('crud_scenario') == 'create_delete':
                        warmup_clean = warmup_clean and len(snapshot(server.database)['articles']) == p.get('fixture_articles', 3)
                    if verification_only and not warmup_clean:
                        row.update(status='failed', reason='Warmup had failed requests, operations, or dropped iterations')
                    elif early_abort_reason:
                        row.update(status='unstable', reason=early_abort_reason)
                    elif not ready and not verification_only and not p.get('allow_unstable'):
                        row.update(status='unstable', reason='No stable window within budget')
                    elif deadline - time.monotonic() < p['measurement_seconds']:
                        row.update(status='not_run', reason='Insufficient remaining measurement budget')
                    else:
                        is_crud = p.get('k6_script') == 'bench/k6/crud.js'
                        before = snapshot(server.database) if is_crud else None
                        sequence_before = article_sequence(server.database) if is_crud else None
                        collector = None
                        telemetry = {'status': 'unavailable', 'reason': 'Collector could not be started'}
                        try:
                            import collect
                            if not p.get('capacity_search'):
                                if p.get('measurement_slo_required'):
                                    measurement_cpu_before = server.record_cpu('measurement-cpu-before.json')
                                collector = collect.ResourceCollector(server.name, interval=1.0)
                                collector.start()
                        except Exception as e:
                            if p.get('capacity_search'):
                                raise RuntimeError('App telemetry collector unavailable: ' + str(e)) from e
                            telemetry = {'status': 'unavailable', 'reason': str(e)}
                        try:
                            if p.get('capacity_search'):
                                recovery_needed = False
                                def measure(rate, duration, phase):
                                    nonlocal telemetry, recovery_needed
                                    if time.monotonic() + duration > deadline:
                                        raise RuntimeError('Insufficient remaining capacity measurement budget')
                                    if recovery_needed and p.get('recovery_probe_seconds'):
                                        recover(server, trial['endpoint'], p, cpus, directory / f'recovery-{len(steps_seen):03d}')
                                        recovery_needed = False
                                    step_dir = directory / f'{len(steps_seen):03d}-{phase}-{rate:g}'
                                    steps_seen.append(str(step_dir))
                                    save(directory / 'progress.json', {'target': trial['target'], 'repetition': trial['repetition'], 'phase': phase, 'rate': rate, 'step': len(steps_seen)})
                                    step_collector = collect.ResourceCollector(server.name, interval=1.0)
                                    before_cpu = server.record_cpu(f'{len(steps_seen):03d}-cpu-before.json')
                                    step_collector.start()
                                    try:
                                        measured_step = sample(server, trial['endpoint'], duration, p, cpus, step_dir, rate=rate, phase=phase)
                                    finally:
                                        step_telemetry = step_collector.stop()
                                        after_cpu = server.record_cpu(f'{len(steps_seen):03d}-cpu-after.json')
                                        a = throttled_count(after_cpu.get('cpu.stat'))
                                        b = throttled_count(before_cpu.get('cpu.stat'))
                                        step_telemetry['summary']['throttled_periods_delta'] = a - b if a is not None and b is not None else None
                                        save(step_dir / 'app-telemetry.json', step_telemetry)
                                        if phase == 'confirm':
                                            telemetry = step_telemetry
                                    save(directory / 'last-step.json', {'phase': phase, 'rate': rate, 'measurement': measured_step})
                                    recovery_needed = capacity.decision(measured_step, p) != 'pass'
                                    return measured_step
                                steps_seen = []
                                trial_p = dict(p)
                                target_start = (p.get('target_capacity_start_rps') or {}).get(trial['target'])
                                if target_start:
                                    trial_p['capacity_start_rps'] = target_start
                                try:
                                    capacity_result = capacity.search(measure, trial_p)
                                except Exception as e:
                                    save(directory / 'capacity-search.json', {
                                        'status': 'error',
                                        'error': str(e),
                                        'capacity_steps': steps_seen,
                                        'steps': getattr(e, 'steps', [])
                                    })
                                    raise
                                save(directory / 'capacity-search.json', capacity_result)
                                row['capacity_interval'] = {key: capacity_result.get(key) for key in
                                    ('confirmed_lower_rps', 'failed_upper_rps', 'failed_upper_seconds', 'resolution_rps', 'tolerance_rps', 'status')}
                                row['capacity_rps'] = capacity_result['capacity_rps']
                                row['offered_rps'] = capacity_result['offered_rps']
                                row['capacity_steps'] = steps_seen
                                measured = capacity_result['measurement']
                            else:
                                measured = sample(server, trial['endpoint'], p['measurement_seconds'], p, cpus, directory / 'measurement' if p.get('retain_phase_artifacts') else directory, phase='measurement')
                        finally:
                            if collector:
                                telemetry = collector.stop()
                                if p.get('measurement_slo_required'):
                                    after_cpu = server.record_cpu('measurement-cpu-after.json')
                                    before_throttle = throttled_count(measurement_cpu_before.get('cpu.stat'))
                                    after_throttle = throttled_count(after_cpu.get('cpu.stat'))
                                    telemetry['summary']['throttled_periods_delta'] = (
                                        after_throttle - before_throttle if before_throttle is not None and after_throttle is not None else None)
                        save(directory / 'telemetry.json', telemetry)
                        row.update(measurement=measured, telemetry=telemetry)
                        if is_crud:
                            row['database_check'] = verify_crud_state(
                                before, snapshot(server.database), sequence_before,
                                article_sequence(server.database), measured,
                                p.get('crud_scenario', 'mix'), p.get('fixture_articles', 3))
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
                            operations.get('failed', 0) > 0) or measured.get('client_saturated', False) or (
                            measured.get('iterations_dropped', 0) > 0)
                        if is_crud:
                            has_errors = has_errors or failed_reqs > 0
                            scenario = p.get('crud_scenario', 'mix')
                            if scenario != 'read':
                                has_errors = has_errors or operations.get('writes', 0) == 0
                            if scenario == 'mix':
                                has_errors = has_errors or operations.get('reads', 0) == 0
                        if p.get('capacity_search'):
                            has_errors = (has_errors or capacity_result['status'] != 'pass' or
                                telemetry.get('summary', {}).get('sample_count', 0) == 0 or
                                telemetry.get('summary', {}).get('throttled_periods_delta') != 0 or
                                telemetry.get('summary', {}).get('oom_killed', False))
                        if p.get('measurement_slo_required'):
                            row['measurement_classification'] = capacity.classify(measured, p)
                            has_errors = (has_errors or row['measurement_classification']['category'] != 'slo_pass' or
                                telemetry.get('summary', {}).get('sample_count', 0) == 0 or
                                telemetry.get('summary', {}).get('throttled_periods_delta') != 0 or
                                telemetry.get('summary', {}).get('oom_killed', False))
                        final_status = 'failed' if has_errors else (
                            'verified' if verification_only else ('unstable' if not ready else 'passed'))
                        row['status'] = final_status
                        if diag_data:
                            row.update(diagnostics=diag_data)
                    save(directory / 'trial.json', row)
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
                status = 'transport_or_artifact_error' if 'artifact transfer' in str(e).lower() else 'failed'
                row.update(status=status, reason=str(e))
            except (KeyboardInterrupt, SystemExit):
                row.update(status='interrupted', reason='Benchmark interrupted')
                raise
            finally:
                row['finished_at'] = time.time()
                row['evidence_directory'] = str(directory)
                save(directory / 'trial.json', row)
                save(output / 'per-run.json', result)
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
    parser.add_argument('--remote-loadgen', help='GCE tester VM name; run controller on app VM')
    parser.add_argument('--gce-zone')
    parser.add_argument('--gce-project')
    parser.add_argument('--target-port', type=int)
    parser.add_argument('--target-host', help='Hostname or IP for load generator connection (default: 127.0.0.1)')
    parser.add_argument('--env-file', help='Path to environment configuration file (e.g. bench/environments/local-single-host.env)')
    parser.add_argument('--dry-run',action='store_true', help='Validate configuration and print execution plan without running')
    parser.add_argument('--preflight-file', help='Path to existing preflight.json to reuse')
    parser.add_argument('--seed', type=int, help='Override random seed for scheduling')
    parser.add_argument('--capacity-start-rps', type=int, help='Override capacity search starting RPS')
    parser.add_argument('--capacity-min-rps', type=int, help='Override capacity search minimum RPS for downward search')
    parser.add_argument('--target-capacity-start-rps', help='Comma-separated target=RPS pairs (e.g. rails-cruby-off=25,spinel=100)')
    parser.add_argument('--max-cv', type=float, help='Override maximum CV threshold for warmup stabilization')
    parser.add_argument('--max-drift', type=float, help='Override maximum drift threshold for warmup stabilization')
    parser.add_argument('--warmup-fail-fast-windows', type=int, help='Consecutive unhealthy windows to trigger warmup early abort')
    parser.add_argument('--warmup-max-latency-ms', type=float, help='Latency threshold (ms) for warmup early abort')
    args = parser.parse_args()

    loaded = load_env_file(args.env_file) if args.env_file else {}
    env_profile = loaded.get('BENCH_PROFILE') or os.environ.get('BENCH_PROFILE')
    env_targets = loaded.get('BENCH_TARGETS') or os.environ.get('BENCH_TARGETS')
    env_output = loaded.get('BENCH_OUTPUT') or os.environ.get('BENCH_OUTPUT')
    env_app_cpus = loaded.get('BENCH_APP_CPUS') or os.environ.get('BENCH_APP_CPUS')
    env_load_cpus = loaded.get('BENCH_LOAD_CPUS') or os.environ.get('BENCH_LOAD_CPUS')
    env_target_host = loaded.get('BENCH_TARGET_HOST') or os.environ.get('BENCH_TARGET_HOST')
    remote = args.remote_loadgen or loaded.get('BENCH_REMOTE_LOADGEN') or os.environ.get('BENCH_REMOTE_LOADGEN')
    zone = args.gce_zone or loaded.get('BENCH_GCE_ZONE') or os.environ.get('BENCH_GCE_ZONE')
    project = args.gce_project or loaded.get('BENCH_GCE_PROJECT') or os.environ.get('BENCH_GCE_PROJECT')
    port = args.target_port or loaded.get('BENCH_TARGET_PORT') or os.environ.get('BENCH_TARGET_PORT')
    env_memory_mb = loaded.get('BENCH_MEMORY_MB') or os.environ.get('BENCH_MEMORY_MB')
    env_seed = loaded.get('BENCH_SEED') or os.environ.get('BENCH_SEED')
    env_capacity_start_rps = loaded.get('BENCH_CAPACITY_START_RPS') or os.environ.get('BENCH_CAPACITY_START_RPS')
    env_capacity_min_rps = loaded.get('BENCH_CAPACITY_MIN_RPS') or os.environ.get('BENCH_CAPACITY_MIN_RPS')
    env_target_capacity_start_rps = loaded.get('BENCH_TARGET_CAPACITY_START_RPS') or os.environ.get('BENCH_TARGET_CAPACITY_START_RPS')
    env_max_cv = loaded.get('BENCH_MAX_CV') or os.environ.get('BENCH_MAX_CV')
    env_max_drift = loaded.get('BENCH_MAX_DRIFT') or os.environ.get('BENCH_MAX_DRIFT')
    env_fail_fast_windows = loaded.get('BENCH_WARMUP_FAIL_FAST_WINDOWS') or os.environ.get('BENCH_WARMUP_FAIL_FAST_WINDOWS')
    env_max_latency = loaded.get('BENCH_WARMUP_MAX_LATENCY_MS') or os.environ.get('BENCH_WARMUP_MAX_LATENCY_MS')

    output_path = args.output
    if args.output.startswith('bench-results/') and env_output:
        output_path = env_output

    if args.action == 'report':
        print(json.dumps(report(output_path),indent=2,ensure_ascii=False)); return 0

    profile_path = args.profile
    if args.profile == str(ROOT/'bench/profiles/quick.yml') and env_profile:
        profile_path = env_profile

    p = config(profile_path)
    start_rps_val = args.capacity_start_rps or (int(env_capacity_start_rps) if env_capacity_start_rps else None)
    if start_rps_val:
        p['capacity_start_rps'] = start_rps_val
    min_rps_val = args.capacity_min_rps or (int(env_capacity_min_rps) if env_capacity_min_rps else None)
    if min_rps_val:
        p['capacity_min_rps'] = min_rps_val
    target_start_val = args.target_capacity_start_rps or env_target_capacity_start_rps
    if target_start_val:
        p_target_starts = p.get('target_capacity_start_rps', {}).copy()
        for pair in target_start_val.split(','):
            if '=' in pair:
                t_name, t_rps = pair.split('=', 1)
                p_target_starts[t_name.strip()] = int(t_rps.strip())
        p['target_capacity_start_rps'] = p_target_starts
    max_cv_val = args.max_cv or (float(env_max_cv) if env_max_cv else None)
    if max_cv_val:
        p['max_cv'] = max_cv_val
    max_drift_val = args.max_drift or (float(env_max_drift) if env_max_drift else None)
    if max_drift_val:
        p['max_drift'] = max_drift_val
    ffw_val = args.warmup_fail_fast_windows or (int(env_fail_fast_windows) if env_fail_fast_windows else None)
    if ffw_val:
        p['warmup_fail_fast_windows'] = ffw_val
    max_lat_val = args.warmup_max_latency_ms or (float(env_max_latency) if env_max_latency else None)
    if max_lat_val:
        p['warmup_max_latency_ms'] = max_lat_val
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
    if remote:
        if not zone or not project or not th_val or th_val in ('127.0.0.1', 'localhost', 'APP_PRIVATE_IP') or project == 'PROJECT_ID':
            if not args.dry_run:
                raise ValueError('Remote mode needs real app private IP, zone and project')
        p.update(remote_loadgen=remote, gce_zone=zone, gce_project=project, target_port=int(port or 3000))
    elif th_val and th_val not in ('127.0.0.1', 'localhost') and not args.dry_run:
        raise ValueError('Nonlocal target_host requires --remote-loadgen')
    seed_val = args.seed if args.seed is not None else (int(env_seed) if env_seed is not None else None)
    if seed_val is not None:
        p['seed'] = seed_val

    app_cpus = args.app_cpus or env_app_cpus
    load_cpus = args.load_cpus or env_load_cpus
    cpus = allocation(p, app_cpus, load_cpus, remote=bool(remote), dry_run=args.dry_run)
    plan = {'profile':p,'cpus':cpus,'schedule':schedule(p),'action':args.action,
            'reference':TARGETS['reference'],'targets':TARGETS}
    if args.dry_run:
        print(json.dumps(plan,indent=2)); return 0
    output = Path(output_path).resolve(); output.mkdir(parents=True,exist_ok=False)
    save(output/'plan.json',plan)
    environment = {'schema_version':1,'platform':platform.platform(),'python':sys.version,
         'cpuinfo':Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else '',
         'cpus':cpus,
         'git_commit':command(['git','rev-parse','HEAD']), 'git_status':command(['git','status','--porcelain']),
         'profile_sha256':hashlib.sha256(Path(profile_path).read_bytes()).hexdigest(),
         'target_sha256':hashlib.sha256((ROOT/'bench/targets.yml').read_bytes()).hexdigest(),
         'docker_version':command(['docker','version','--format','{{json .}}'], check=False) if shutil.which('docker') else None,
         'cpu_smt_siblings':{str(c):Path(f'/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list').read_text().strip()
             for c in cpus['app']+cpus['client'] if Path(f'/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list').exists()}}
    if remote:
        environment['tester_environment'] = RemoteLoadGenerator(remote, zone, project).probe()
        environment['private_rtt'] = command(['ping', '-c', '5', th_val], timeout=15, check=False) if shutil.which('ping') else 'ping unavailable'
        environment['app_environment'] = command(['uname', '-a'])
    environment['image_ids'] = {stage: command(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'rails-aot-bench:' + stage], check=False)
        for stage in sorted({TARGETS['targets'][t]['image'] for t in p['targets']})} if shutil.which('docker') else {}
    save(output/'env.json', environment)
    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    try:
        if args.action == 'build':
            build(list(dict.fromkeys([TARGETS['reference']]+p['targets'])),output)
        else:
            if getattr(args, 'preflight_file', None):
                checks = reuse_preflight(args.preflight_file, p['targets'])
                reused = Path(args.preflight_file)
                (output / 'preflight').mkdir(parents=True, exist_ok=True)
                shutil.copyfile(reused, output / 'preflight/preflight.json')
                shutil.copyfile(reused.with_name('preflight-manifest.json'),
                                output / 'preflight/preflight-manifest.json')
            else:
                checks_dir = output if args.action == 'preflight' else output/'preflight'
                checks = preflight(p['targets'],checks_dir,p,cpus)
                save_preflight_manifest(checks_dir/'preflight.json', p['targets'])
            if args.action == 'run' and p.get('k6_script') == 'bench/k6/crud.js':
                missing = crud_gate(checks, p['targets'], p.get('crud_scenario', 'mix'))
                if missing:
                    raise RuntimeError(f'CRUD preflight requires matching successful operations: {missing}')
            if args.action == 'run':
                rows = trials(p,cpus,output/'trials',checks)
                save(output/'summary.json',{'purpose':'Benchmark trial execution summary',
                    'passed':sum(r['status']=='passed' for r in rows),'total':len(rows)})
                report(output)
                allowed_statuses = ('verified',) if p.get('verification_only') else (
                    ('passed', 'excluded', 'unstable') if p.get('allow_unstable') else ('passed', 'excluded'))
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

#!/usr/bin/env python3
"""Diagnostic analysis engine for JRuby compile ON/OFF and CRuby YJIT interactions.

Analyzes JIT compilation logs, JVM GC/compilation MXBeans, YJIT runtime stats,
and warmup stabilization time-series across 2x2 runtime matrices.
"""
import argparse
import io
import json
import math
import re
import statistics
from pathlib import Path

def parse_diagnostic_artifacts(root_path):
    """Load trials, warmup windows, diagnostics, and telemetry from benchmark results."""
    root = Path(root_path)
    preflight_file = root / 'preflight/preflight.json'
    if not preflight_file.exists():
        preflight_file = root / 'preflight.json'
    checks = json.loads(preflight_file.read_text(encoding='utf-8')) if preflight_file.exists() else {}

    trials_file = root / 'trials/per-run.json'
    if not trials_file.exists():
        trials_file = root / 'per-run.json'
    trial_rows = json.loads(trials_file.read_text(encoding='utf-8')) if trials_file.exists() else []

    trials_dir = root / 'trials'
    trials_details = []
    if trials_dir.is_dir():
        for d in sorted(trials_dir.iterdir()):
            if d.is_dir():
                record = {'dir': str(d.name)}
                target_from_dir = d.name.split('-', 1)[1] if '-' in d.name else 'unknown'
                trial_json = d / 'trial.json'
                if trial_json.exists():
                    record['trial'] = json.loads(trial_json.read_text(encoding='utf-8'))
                else:
                    authoritative = next((row for index, row in enumerate(trial_rows)
                        if d.name == f"{index:04d}-{row.get('target')}"), None)
                    record['trial'] = authoritative or {'target': target_from_dir,
                        'repetition': None, 'status': 'missing', 'endpoint': None}
                    record['missing'] = ['trial.json']
                warmup_json = d / 'warmup.json'
                if warmup_json.exists():
                    record['warmup'] = json.loads(warmup_json.read_text(encoding='utf-8'))
                    for index, window in enumerate(record['warmup']):
                        log = d / f'warmup-{index:03d}' / 'k6.log'
                        if 'failure_counts' in window or not log.exists():
                            continue
                        # Direct per-request log evidence, never latency inference.
                        text = log.read_text(encoding='utf-8', errors='replace')
                        timeouts = sum(bool(re.search(r'msg="Request Failed".*error="(?:.*: )?request timeout"$', line))
                                       for line in text.splitlines())
                        failed = window.get('requests_failed', window.get('errors', 0))
                        if timeouts and timeouts <= failed:
                            window['failure_counts'] = {'timeout': timeouts}
                            window['failure_counts_evidence'] = str(log.relative_to(root))
                        elif timeouts > failed:
                            record.setdefault('missing', []).append(f'{log.relative_to(root)}: failure-count mismatch')
                diag_json = d / 'diagnostics.json'
                if diag_json.exists():
                    record['diagnostics'] = json.loads(diag_json.read_text(encoding='utf-8'))
                telemetry_json = d / 'telemetry.json'
                if telemetry_json.exists():
                    record['telemetry'] = json.loads(telemetry_json.read_text(encoding='utf-8'))
                cpu_start_json = d / 'cpu-start.json'
                if cpu_start_json.exists():
                    record['cpu_start'] = json.loads(cpu_start_json.read_text(encoding='utf-8'))
                cpu_end_json = d / 'cpu-end.json'
                if cpu_end_json.exists():
                    record['cpu_end'] = json.loads(cpu_end_json.read_text(encoding='utf-8'))
                trials_details.append(record)

    plan_file = root / 'plan.json'
    plan = json.loads(plan_file.read_text(encoding='utf-8')) if plan_file.exists() else {}

    return checks, trial_rows, trials_details, plan

def compute_ratio(num, denom):
    """Compute ratio safely, returning None if missing, invalid, or zero denominator."""
    if num is None or denom is None:
        return None
    try:
        num_f, denom_f = float(num), float(denom)
        if denom_f <= 0.0 or math.isnan(num_f) or math.isnan(denom_f):
            return None
        return round(num_f / denom_f, 3)
    except (ValueError, TypeError, ZeroDivisionError):
        return None

def container_memory_mb(telemetry):
    value = telemetry.get('peak_container_memory_bytes')
    if value is None:
        value = telemetry.get('peak_rss_bytes')  # Historic docker stats artifact.
    return round(value / (1024 * 1024), 1) if value is not None else None

def analyze_warmup_trajectory(windows, p=None):
    """Analyze warmup progression: initial vs final RPS, latency convergence, time to stability."""
    if not windows:
        return {
            'window_count': 0,
            'total_warmup_seconds': 0.0,
            'initial_rps': None,
            'final_rps': None,
            'rps_growth_pct': None,
            'initial_p95_ms': None,
            'final_p95_ms': None,
            'p95_reduction_pct': None,
            'stabilized': False,
            'stabilized_at_seconds': None,
            'final_cv': None,
            'final_drift': None,
        }

    n_windows = len(windows)
    elapsed = sum(w.get('elapsed', 0.0) for w in windows)
    first_w = windows[0]
    last_w = windows[-1]

    init_rps = first_w.get('rps_successful') or first_w.get('rps')
    final_rps = last_w.get('rps_successful') or last_w.get('rps')
    rps_growth = round(((final_rps - init_rps) / init_rps) * 100, 1) if init_rps and final_rps and init_rps > 0 else None

    init_p95 = first_w.get('latency_ms', {}).get('p95') or first_w.get('p95_ms')
    final_p95 = last_w.get('latency_ms', {}).get('p95') or last_w.get('p95_ms')
    p95_reduct = round(((init_p95 - final_p95) / init_p95) * 100, 1) if init_p95 and final_p95 and init_p95 > 0 else None

    # Check stability window progression
    stable_windows = p.get('stable_windows', 3) if p else 3
    max_cv = p.get('max_cv', 0.1) if p else 0.1
    max_drift = p.get('max_drift', 0.1) if p else 0.1
    min_warmup = p.get('warmup_min_seconds', 15) if p else 15

    stabilized = False
    stabilized_at = None
    final_cv = None
    final_drift = None

    curr_elapsed = 0.0
    for idx, w in enumerate(windows):
        curr_elapsed += w.get('elapsed', 0.0)
        if idx + 1 >= stable_windows and curr_elapsed >= min_warmup:
            sub = windows[idx + 1 - stable_windows : idx + 1]
            rps_vals = [sw.get('rps_successful') or sw.get('rps') or 0.0 for sw in sub]
            if all(v > 0 for v in rps_vals):
                mean_val = statistics.mean(rps_vals)
                cv = statistics.pstdev(rps_vals) / mean_val if mean_val > 0 else 1.0
                half = stable_windows // 2 or 1
                drift = abs(statistics.mean(rps_vals[-half:]) - statistics.mean(rps_vals[:half])) / mean_val if mean_val > 0 else 1.0
                if idx + 1 == n_windows:
                    final_cv = round(cv, 4)
                    final_drift = round(drift, 4)
                # Rate convergence alone cannot certify stable failed traffic.
                clean = all(sw.get('requests_failed', sw.get('errors', 0)) == 0
                            and not sw.get('iterations_dropped', 0) and not sw.get('client_saturated')
                            for sw in sub)
                latency_keys = ('p95', 'p99') if (p or {}).get('capacity_search') else ('p95',)
                latency_stable = True
                for key in latency_keys:
                    values = [sw.get('latency_ms', {}).get(key, sw.get(key + '_ms')) for sw in sub]
                    if any(value is None or value <= 0 for value in values):
                        latency_stable = False
                        break
                    avg = statistics.mean(values)
                    latency_stable &= statistics.pstdev(values)/avg <= max_cv
                    latency_stable &= abs(statistics.mean(values[-half:])-statistics.mean(values[:half]))/avg <= max_drift
                if not stabilized and clean and latency_stable and cv <= max_cv and drift <= max_drift:
                    stabilized = True
                    stabilized_at = round(curr_elapsed, 1)

    return {
        'window_count': n_windows,
        'total_warmup_seconds': round(elapsed, 1),
        'initial_rps': round(init_rps, 1) if init_rps else None,
        'final_rps': round(final_rps, 1) if final_rps else None,
        'rps_growth_pct': rps_growth,
        'initial_p95_ms': round(init_p95, 2) if init_p95 else None,
        'final_p95_ms': round(final_p95, 2) if final_p95 else None,
        'p95_reduction_pct': p95_reduct,
        'stabilized': stabilized,
        'stabilized_at_seconds': stabilized_at,
        'final_cv': final_cv,
        'final_drift': final_drift,
    }

def analyze_failure_taxonomy(windows):
    """Use explicit counters; historic latency tails cannot classify failures."""
    total = sum(w.get('requests_total', 0) for w in windows)
    failed = sum(w.get('requests_failed', w.get('errors', 0)) for w in windows)
    counts = {'timeout_failures': 0, 'network_failures': 0, 'http_failures': 0,
              'integrity_failures': 0, 'unknown_failures': 0}
    for w in windows:
        n = w.get('requests_failed', w.get('errors', 0))
        categories = w.get('failure_counts')
        if categories is None:
            counts['unknown_failures'] += n
            continue
        for field, key in [('timeout_failures', 'timeout'), ('network_failures', 'network'),
                           ('http_failures', 'http_status'), ('integrity_failures', 'response_integrity')]:
            counts[field] += categories.get(key, 0)
        known = sum(categories.get(k, 0) for k in ('timeout', 'network', 'http_status', 'response_integrity'))
        counts['unknown_failures'] += max(0, n - known)
    failed_windows = sum(w.get('requests_failed', w.get('errors', 0)) > 0 for w in windows)
    return dict(counts, total_windows=len(windows), failed_windows=failed_windows,
                failed_windows_pct=round(100 * failed_windows / len(windows), 2) if windows else None,
                total_requests=total, successful_requests=total-failed, failed_requests=failed,
                error_rate_pct=round(100 * failed / total, 3) if total else None,
                client_saturated_windows=sum(bool(w.get('client_saturated')) for w in windows))


def analyze_concurrency_and_queuing(windows, cpu_start=None, cpu_end=None, num_threads=4, target_name=''):
    """Describe observations. HTTP minimum is not measured service/queue time."""
    def values(key):
        return [w['latency_ms'][key] for w in windows if (w.get('latency_ms') or {}).get(key) is not None]
    rates = [w.get('rps_successful', w.get('rps')) for w in windows]
    rates = [v for v in rates if v is not None]
    observed = {'http_min_ms': min(values('min')) if values('min') else None,
                'median_of_window_medians_ms': statistics.median(values('med')) if values('med') else None,
                'mean_window_successful_rps': statistics.mean(rates) if rates else None}
    cpu = {}
    if cpu_start and cpu_end:
        def stat(data):
            return {k: int(v) for k, v in (line.split() for line in (data.get('cpu.stat') if isinstance(data.get('cpu.stat'), str) else '').splitlines())}
        first, last = stat(cpu_start), stat(cpu_end)
        delta = last.get('usage_usec', 0) - first.get('usage_usec', 0)
        # Endpoints bracket the complete trial (including search/orchestration).
        # Without timestamps, dividing by the sum of warmup windows is invalid.
        elapsed = (cpu_end.get('timestamp', 0) - cpu_start.get('timestamp', 0))
        if delta >= 0 and 'usage_usec' in first and 'usage_usec' in last:
            cpu = {'usage_delta_usec': delta,
                   'vcpus_active': round(delta / elapsed / 1e6, 3) if elapsed > 0 else None,
                   'elapsed_seconds': elapsed if elapsed > 0 else None,
                   'pct_user': round(100 * (last.get('user_usec', 0)-first.get('user_usec', 0)) / delta, 2) if delta else None,
                   'pct_system': round(100 * (last.get('system_usec', 0)-first.get('system_usec', 0)) / delta, 2) if delta else None,
                   'throttled_periods': last.get('nr_throttled', 0)-first.get('nr_throttled', 0)}
    return {'concurrency_vus': max((w.get('vus_peak', 0) for w in windows), default=0),
            'threads': num_threads, 'observed': observed,
            'observed_successful_rps': observed['mean_window_successful_rps'],
            'cpu_metrics': cpu, 'root_cause': 'unresolved',
            'estimated_service_time_ms': None, 'estimated_queue_depth': None,
            'estimated_queue_delay_ms': None, 'max_theoretical_throughput_rps': None,
            'hypotheses': ['CPU/allocations', 'DB or connection wait', 'HTTP connection handling',
                           'worker scheduling/GC pauses'],
            'missing_evidence': ['single-request stage timings', 'CPU/wait traces', 'low-load sweep']}


def compute_viable_load_envelope(target_name, service_time_ms=None, num_threads=4,
                                slo_p99_ms=100.0, timeout_ms=5000.0, observations=None, profile=None):
    """Confirm measured points only; never manufacture a safe range from a name."""
    import capacity
    contract = dict(profile or {}, slo_p99_ms=slo_p99_ms)
    contract.setdefault('max_error_rate', 0.001)
    duration = contract.get('measurement_seconds', 120)
    points = []
    for m in observations or []:
        if (m.get('driver') == 'k6-open-arrival' and m.get('elapsed', 0) >= duration
                and m.get('rate_offered') is not None and capacity.decision(m, contract) == 'pass'):
            points.append({'offered_rps': m['rate_offered'], 'elapsed_seconds': m['elapsed'],
                           'p99_ms': m['latency_ms']['p99'], 'requests_failed': m['requests_failed']})
    return {'target': target_name, 'status': 'confirmed_points' if points else 'unconfirmed',
            'confirmed_points': points, 'slo_achievable': True if points else None,
            'safe_open_arrival_rps': None, 'max_viable_concurrency_vus_no_timeout': None,
            'max_viable_concurrency_vus_slo': None, 'service_time_ms': None,
            'notes': 'Only listed measured points passed; unmeasured ranges and causes remain unresolved.'}


def _single_jruby_matrix(trials_by_target, endpoint):
    """Compute JRuby 2x2 matrix: Rails vs Emitted, compile.mode=JIT vs OFF."""
    def get_target_data(target_name):
        entries = trials_by_target.get(target_name, [])
        for e in entries:
            t = e.get('trial', {})
            if t.get('endpoint') == endpoint and t.get('status') in ('passed', 'unstable'):
                m = t.get('measurement', {})
                tel = t.get('telemetry', {}).get('summary', {})
                diag = e.get('diagnostics', {}) or t.get('diagnostics', {})
                end_diag = diag.get('end') or diag.get('warmup') or diag.get('start') or {}
                rps = m.get('rps_successful') or m.get('rps')
                lat = m.get('latency_ms', {})
                p95 = lat.get('p95') or m.get('p95_ms')
                p99 = lat.get('p99') or m.get('p99_ms')
                return {
                    'target': target_name,
                    'status': t.get('status'),
                    'rps': round(rps, 2) if rps else None,
                    'p95_ms': round(p95, 2) if p95 else None,
                    'p99_ms': round(p99, 2) if p99 else None,
                    'mean_cpu_pct': tel.get('mean_cpu_pct'),
                    'peak_container_memory_mb': container_memory_mb(tel),
                    'compile_mode': end_diag.get('compile_mode'),
                    'jvm_compiler': end_diag.get('jvm_compiler'),
                    'jvm_compilation_time_ms': end_diag.get('jvm_compilation_time_ms'),
                    'jvm_gc': end_diag.get('jvm_gc', []),
                    'jvm_memory': end_diag.get('jvm_memory', {}),
                    'jvm_args': end_diag.get('jvm_args', []),
                    'jvm_jit_active': bool(end_diag.get('jvm_compiler') and 'OFF' not in str(end_diag.get('jvm_compiler'))),
                }
        return {'target': target_name, 'status': 'missing', 'rps': None, 'jvm_jit_active': False}

    r_off = get_target_data('rails-jruby-off')
    r_jit = get_target_data('rails-jruby')
    e_off = get_target_data('emit-jruby-off')
    e_jit = get_target_data('emit-jruby')

    # Ratios
    # JRuby compile mode speedup: G = JIT / OFF
    g_rails = compute_ratio(r_jit.get('rps'), r_off.get('rps'))
    g_emitted = compute_ratio(e_jit.get('rps'), e_off.get('rps'))
    interaction = compute_ratio(g_emitted, g_rails)

    # Roundhouse speedups at OFF and JIT
    rh_off = compute_ratio(e_off.get('rps'), r_off.get('rps'))
    rh_jit = compute_ratio(e_jit.get('rps'), r_jit.get('rps'))

    return {
        'endpoint': endpoint,
        'cells': {
            'rails_off': r_off,
            'rails_jit': r_jit,
            'emitted_off': e_off,
            'emitted_jit': e_jit,
        },
        'jruby_speedup_g': {
            'rails': g_rails,
            'emitted': g_emitted,
            'interaction_ratio': interaction,
        },
        'roundhouse_speedup': {
            'jruby_compile_off': rh_off,
            'jruby_compile_jit': rh_jit,
        },
        'jvm_jit_verified_active_across_all': all([
            r_off.get('jvm_jit_active'),
            r_jit.get('jvm_jit_active'),
            e_off.get('jvm_jit_active'),
            e_jit.get('jvm_jit_active'),
        ]),
    }

def _single_cruby_matrix(trials_by_target, endpoint):
    """Compute CRuby / YJIT 2x2 matrix: Rails vs Emitted, YJIT ON vs OFF."""
    def get_target_data(target_name):
        entries = trials_by_target.get(target_name, [])
        for e in entries:
            t = e.get('trial', {})
            if t.get('endpoint') == endpoint and t.get('status') in ('passed', 'unstable'):
                m = t.get('measurement', {})
                tel = t.get('telemetry', {}).get('summary', {})
                diag = e.get('diagnostics', {}) or t.get('diagnostics', {})
                end_diag = diag.get('end') or diag.get('warmup') or diag.get('start') or {}
                rps = m.get('rps_successful') or m.get('rps')
                lat = m.get('latency_ms', {})
                p95 = lat.get('p95') or m.get('p95_ms')
                p99 = lat.get('p99') or m.get('p99_ms')
                return {
                    'target': target_name,
                    'status': t.get('status'),
                    'rps': round(rps, 2) if rps else None,
                    'p95_ms': round(p95, 2) if p95 else None,
                    'p99_ms': round(p99, 2) if p99 else None,
                    'mean_cpu_pct': tel.get('mean_cpu_pct'),
                    'peak_container_memory_mb': container_memory_mb(tel),
                    'yjit_enabled': end_diag.get('yjit_enabled'),
                    'yjit_stats': end_diag.get('yjit_stats'),
                    'gc_stat': end_diag.get('gc_stat'),
                    'gc_count': end_diag.get('gc_count'),
                }
        return {'target': target_name, 'status': 'missing', 'rps': None}

    r_off = get_target_data('rails-cruby-off')
    r_on = get_target_data('rails-cruby-yjit')
    e_off = get_target_data('emit-cruby-off')
    e_on = get_target_data('emit-cruby-yjit')

    g_rails = compute_ratio(r_on.get('rps'), r_off.get('rps'))
    g_emitted = compute_ratio(e_on.get('rps'), e_off.get('rps'))
    interaction = compute_ratio(g_emitted, g_rails)

    rh_off = compute_ratio(e_off.get('rps'), r_off.get('rps'))
    rh_on = compute_ratio(e_on.get('rps'), r_on.get('rps'))

    return {
        'endpoint': endpoint,
        'cells': {
            'rails_off': r_off,
            'rails_yjit': r_on,
            'emitted_off': e_off,
            'emitted_yjit': e_on,
        },
        'yjit_speedup_g': {
            'rails': g_rails,
            'emitted': g_emitted,
            'interaction_ratio': interaction,
        },
        'roundhouse_speedup': {
            'yjit_off': rh_off,
            'yjit_on': rh_on,
        },
    }



def _repeated_matrix(grouped, endpoint, single, jit_field, numerator_keys, shape_pairs, profile=None):
    contract = profile or {"slo_p99_ms": 100, "max_error_rate": 0.001, "measurement_seconds": 120}
    import capacity
    reps = sorted({r['trial']['repetition'] for rows in grouped.values() for r in rows
                   if r['trial'].get('endpoint') == endpoint and r['trial'].get('repetition') is not None})
    matrices = [single({name: [r for r in rows if r['trial'].get('repetition') == rep]
                        for name, rows in grouped.items()}, endpoint) for rep in reps]
    base = single({}, endpoint)
    base['per_repetition'] = [{'repetition': rep, 'cells': m['cells']} for rep, m in zip(reps, matrices)]
    for key, empty in base['cells'].items():
        cells = [m['cells'][key] for m in matrices]
        observed = [c for c in cells if c.get('status') == 'passed' and c.get('rps') is not None]
        cell = dict(empty)
        # Do not display one repetition's compiler/GC/resource probe as the
        # aggregate. Keep only uniform identity fields; full probes are above.
        for field in cell:
            values = [c.get(field) for c in observed]
            if values and all(value == values[0] for value in values):
                cell[field] = values[0]
            elif field not in ('target', 'rps'):
                cell[field] = {} if isinstance(cell[field], dict) else ([] if isinstance(cell[field], list) else None)
        cell['rps'] = statistics.median(c['rps'] for c in observed) if observed else None
        cell['observed_repetitions'] = len(observed)
        cell['planned_repetitions'] = len(reps)
        cell['metric'] = 'median of passed per-trial observations; see every repetition'
        base['cells'][key] = cell
    valid = []
    for name, records in grouped.items():
        for r in records:
            t = r['trial']; m = t.get('measurement', {})
            if (t.get('endpoint') == endpoint and t.get('status') == 'passed'
                    and t.get('capacity_rps') and t.get('repetition') is not None
                    and m.get('driver') == 'k6-open-arrival' and m.get('elapsed', 0) >= contract.get('measurement_seconds', 120)
                    and capacity.decision(m, contract) == 'pass'):
                valid.append(t)
    pairs = {}
    for label, a, b in numerator_keys + shape_pairs:
        pairs[label] = capacity.paired_ratios(valid, a, b)
    base[jit_field] = {label: pairs[label]['median'] for label, _, _ in numerator_keys}
    base['roundhouse_speedup'] = {label: pairs[label]['median'] for label, _, _ in shape_pairs}
    left, right = (pairs[label]['pairs'] for label, _, _ in numerator_keys)
    lmap = {p['repetition']: p['ratio'] for p in left}
    rmap = {p['repetition']: p['ratio'] for p in right}
    interactions = [rmap[rep]/lmap[rep] for rep in sorted(lmap.keys() & rmap.keys())]
    base[jit_field]['interaction_ratio'] = statistics.median(interactions) if interactions else None
    base['paired_capacity_evidence'] = pairs
    if 'jvm_jit_verified_active_across_all' in base:
        base['jvm_jit_verified_active_across_all'] = bool(matrices) and all(m['jvm_jit_verified_active_across_all'] for m in matrices)
    return base


def analyze_jruby_matrix(grouped, endpoint, profile=None):
    return _repeated_matrix(grouped, endpoint, _single_jruby_matrix, 'jruby_speedup_g',
        [('rails', 'rails-jruby', 'rails-jruby-off'), ('emitted', 'emit-jruby', 'emit-jruby-off')],
        [('jruby_compile_off', 'emit-jruby-off', 'rails-jruby-off'), ('jruby_compile_jit', 'emit-jruby', 'rails-jruby')], profile)


def analyze_cruby_matrix(grouped, endpoint, profile=None):
    return _repeated_matrix(grouped, endpoint, _single_cruby_matrix, 'yjit_speedup_g',
        [('rails', 'rails-cruby-yjit', 'rails-cruby-off'), ('emitted', 'emit-cruby-yjit', 'emit-cruby-off')],
        [('yjit_off', 'emit-cruby-off', 'rails-cruby-off'), ('yjit_on', 'emit-cruby-yjit', 'rails-cruby-yjit')], profile)


def generate_diagnostic_markdown(data):
    """Generate Markdown report for diagnostics with alerts and epistemological groupings."""
    lines = []
    lines.append('# Benchmark P1 JIT Runtime Diagnostics Report\n')
    lines.append('> [!WARNING]')
    lines.append(f"> **Instrumentation Overhead Active**: diagnostics configured = {data.get('profile', {}).get('diagnostics', False)}. Verify actual flags in runtime artifacts; instrumented and uninstrumented runs are separate cohorts.")
    lines.append('> These instrumentation probes incur non-trivial CPU and memory overhead.')
    lines.append('> **DO NOT** mix diagnostic throughput / latency figures with production baseline benchmark rankings from `quick.yml` or `full.yml`.\n')
    if data.get('fixed_offered_rate'):
        lines.append('> Fixed offered RPS provides diagnostic response times and compiler state; capacity speedups and interaction ratios are unavailable without a rate sweep.\n')

    lines.append('> [!IMPORTANT]')
    lines.append('> **JRuby compile.mode=OFF vs JVM JIT**: `compile.mode=OFF` only instructs JRuby to interpret its IR/AST rather than emitting Java bytecode.')
    lines.append('> Check runtime/JVM evidence per trial; compile.mode=OFF does not by itself disable HotSpot JIT. Missing compiler evidence remains unknown.\n')

    # 1. JRuby 2x2 Matrix
    lines.append('## 1. JRuby Compile Mode 2x2 Matrix & Interactions\n')
    for jm in data.get('jruby_matrices', []):
        ep = jm['endpoint']
        c = jm['cells']
        lines.append(f'### Endpoint: `{ep}`\n')
        lines.append('| Shape | JRuby compile.mode=OFF | JRuby compile.mode=JIT | Speedup Factor $G_{{JRuby}}$ ($JIT / OFF$) |')
        lines.append('| --- | --- | --- | --- |')

        r_off_str = f"{c['rails_off']['rps']} RPS" if c['rails_off']['rps'] else '-'
        r_jit_str = f"{c['rails_jit']['rps']} RPS" if c['rails_jit']['rps'] else '-'
        g_r = f"**{jm['jruby_speedup_g']['rails']}x**" if jm['jruby_speedup_g']['rails'] else '-'
        lines.append(f"| **Rails (Baseline)** | {r_off_str} | {r_jit_str} | {g_r} |")

        e_off_str = f"{c['emitted_off']['rps']} RPS" if c['emitted_off']['rps'] else '-'
        e_jit_str = f"{c['emitted_jit']['rps']} RPS" if c['emitted_jit']['rps'] else '-'
        g_e = f"**{jm['jruby_speedup_g']['emitted']}x**" if jm['jruby_speedup_g']['emitted'] else '-'
        lines.append(f"| **Roundhouse Emitted** | {e_off_str} | {e_jit_str} | {g_e} |")

        rh_off_str = f"**{jm['roundhouse_speedup']['jruby_compile_off']}x**" if jm['roundhouse_speedup']['jruby_compile_off'] else '-'
        rh_jit_str = f"**{jm['roundhouse_speedup']['jruby_compile_jit']}x**" if jm['roundhouse_speedup']['jruby_compile_jit'] else '-'
        inter_str = f"**{jm['jruby_speedup_g']['interaction_ratio']}**" if jm['jruby_speedup_g']['interaction_ratio'] else '-'
        lines.append(f"| **Roundhouse Speedup** | {rh_off_str} | {rh_jit_str} | **Interaction $I_{{JRuby}}$: {inter_str}** |")
        lines.append('')

        lines.append('#### JVM JIT Verification Evidence')
        lines.append('| Target | Compile Mode | JVM Compiler | Compilation Time | JVM Args Sample |')
        lines.append('| --- | --- | --- | --- | --- |')
        for key in ('rails_off', 'rails_jit', 'emitted_off', 'emitted_jit'):
            cell = c[key]
            tname = cell.get('target', key)
            cmode = cell.get('compile_mode', '-')
            compiler = cell.get('jvm_compiler', '-')
            ctime = f"{cell.get('jvm_compilation_time_ms', '-')} ms" if cell.get('jvm_compilation_time_ms') is not None else '-'
            args_sample = ' '.join(cell.get('jvm_args', [])[:3]) or '-'
            lines.append(f"| `{tname}` | `{cmode}` | `{compiler}` | {ctime} | `{args_sample}` |")
        lines.append('')

    # 2. CRuby / YJIT 2x2 Matrix
    lines.append('## 2. CRuby / YJIT 2x2 Matrix & Interactions\n')
    for cm in data.get('cruby_matrices', []):
        ep = cm['endpoint']
        c = cm['cells']
        lines.append(f'### Endpoint: `{ep}`\n')
        lines.append('| Shape | CRuby YJIT OFF | CRuby YJIT ON | Speedup Factor $G_{{CRuby}}$ ($YJIT / OFF$) |')
        lines.append('| --- | --- | --- | --- |')

        r_off_str = f"{c['rails_off']['rps']} RPS" if c['rails_off']['rps'] else '-'
        r_on_str = f"{c['rails_yjit']['rps']} RPS" if c['rails_yjit']['rps'] else '-'
        g_r = f"**{cm['yjit_speedup_g']['rails']}x**" if cm['yjit_speedup_g']['rails'] else '-'
        lines.append(f"| **Rails (Baseline)** | {r_off_str} | {r_on_str} | {g_r} |")

        e_off_str = f"{c['emitted_off']['rps']} RPS" if c['emitted_off']['rps'] else '-'
        e_on_str = f"{c['emitted_yjit']['rps']} RPS" if c['emitted_yjit']['rps'] else '-'
        g_e = f"**{cm['yjit_speedup_g']['emitted']}x**" if cm['yjit_speedup_g']['emitted'] else '-'
        lines.append(f"| **Roundhouse Emitted** | {e_off_str} | {e_on_str} | {g_e} |")

        rh_off_str = f"**{cm['roundhouse_speedup']['yjit_off']}x**" if cm['roundhouse_speedup']['yjit_off'] else '-'
        rh_on_str = f"**{cm['roundhouse_speedup']['yjit_on']}x**" if cm['roundhouse_speedup']['yjit_on'] else '-'
        inter_str = f"**{cm['yjit_speedup_g']['interaction_ratio']}**" if cm['yjit_speedup_g']['interaction_ratio'] else '-'
        lines.append(f"| **Roundhouse Speedup** | {rh_off_str} | {rh_on_str} | **Interaction $I_{{CRuby}}$: {inter_str}** |")
        lines.append('')

        lines.append('#### YJIT & GC Internal Statistics')
        lines.append('| Target | YJIT Enabled | Compiled Blocks | Invalidation Count | Ratio in YJIT | GC Minor/Major | Total Alloc Objects |')
        lines.append('| --- | --- | --- | --- | --- | --- | --- |')
        for key in ('rails_off', 'rails_yjit', 'emitted_off', 'emitted_yjit'):
            cell = c[key]
            tname = cell.get('target', key)
            y_en = 'Unknown' if cell.get('yjit_enabled') is None else ('Yes' if cell['yjit_enabled'] else 'No')
            ystat = cell.get('yjit_stats') or {}
            c_blocks = ystat.get('compiled_block_count', '-')
            inv = ystat.get('invalidation_count', '-')
            ratio = f"{ystat.get('ratio_in_yjit', 0) * 100:.1f}%" if 'ratio_in_yjit' in ystat else '-'
            gc = cell.get('gc_stat') or {}
            minor_maj = f"{gc.get('minor_gc_count', '-')}/{gc.get('major_gc_count', '-')}" if 'minor_gc_count' in gc else '-'
            alloc = gc.get('total_allocated_objects', '-')
            lines.append(f"| `{tname}` | {y_en} | {c_blocks} | {inv} | {ratio} | {minor_maj} | {alloc} |")
        lines.append('')

    # 3. Warmup Progression & Convergence
    lines.append('## 3. Warmup Progression & Stabilization Analysis\n')
    lines.append('| Target | Endpoint | Windows | Warmup Time | Initial RPS | Final RPS | RPS Growth % | Initial p95 (ms) | Final p95 (ms) | Stabilized? | Time to Stability | Final CV |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for traj in data.get('warmup_trajectories', []):
        tname = traj['target']
        ep = traj['endpoint']
        w = traj['warmup_analysis']
        n_w = w['window_count']
        tot_s = f"{w['total_warmup_seconds']}s"
        i_rps = w['initial_rps'] or '-'
        f_rps = w['final_rps'] or '-'
        g_pct = f"+{w['rps_growth_pct']}%" if w['rps_growth_pct'] and w['rps_growth_pct'] > 0 else (f"{w['rps_growth_pct']}%" if w['rps_growth_pct'] is not None else '-')
        i_p95 = w['initial_p95_ms'] or '-'
        f_p95 = w['final_p95_ms'] or '-'
        stab = '✅ Yes' if w['stabilized'] else '⚠️ No'
        stab_s = f"{w['stabilized_at_seconds']}s" if w['stabilized_at_seconds'] else '-'
        cv = f"{w['final_cv']:.3f}" if w['final_cv'] is not None else '-'
        lines.append(f"| `{tname}` | `{ep}` | {n_w} | {tot_s} | {i_rps} | {f_rps} | {g_pct} | {i_p95} | {f_p95} | {stab} | {stab_s} | {cv} |")
    lines.append('')

    # 4. Epistemological Classification
    lines.append('## 4. Epistemological Classification (観測の分類と解釈)\n')

    lines.append('### A. 実測事実 (Observed Facts)')
    lines.append('- Runtime/JIT identity must be verified in each repetition’s artifacts; missing probes are unknown.')
    lines.append('- Matrix RPS is the median of passed observations. See `diagnostics.json` → `per_repetition` for every trial and `paired_capacity_evidence` for eligible pairs.')
    lines.append('')
    lines.append('### B. 説明を支持する観測 (Supporting Observations)')
    lines.append('- Allocation and warmup differences require aligned intervals, repeated evidence and explicit instrumentation flags; this report does not assert their cause.')
    lines.append('')

    lines.append('### C. 未検証の仮説 (Unverified Hypotheses)')
    lines.append('- **Interaction Mechanisms**: Whether the interaction ratio $I = G_{emitted} / G_{Rails}$ deviation from 1.0 is primarily governed by reduced method call dispatch overhead, inline cache stabilization, or simplified basic block structures remains an unverified hypothesis requiring instruction-level profiling.')
    lines.append('- **Spinel Architectural Scope**: Spinel speedup is a whole-system architectural difference (embedded HTTP server, SQLite C-adapter, compile-time schema specialization, and native AOT) and cannot be construed as a standalone Ruby-to-AOT transformation multiplier.')
    lines.append('')

    if data.get('failure_diagnoses'):
        lines.extend(generate_failure_diagnosis_markdown(data['failure_diagnoses']))

    return '\n'.join(lines) + '\n'

def generate_failure_diagnosis_markdown(failure_diagnoses):
    if not failure_diagnoses:
        return []
    lines = ['## 5. Runtime Failure Diagnosis and Viable Load Envelopes (Issues #54 & #55)', '',
             'Root causes remain unresolved without CPU/wait traces. Historic counters without explicit failure categories are unknown.', '',
             '| Target | Reps | Windows total/failed | Requests total/failed | Error % | Explicit timeouts | Unknown failures |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name, d in sorted(failure_diagnoses.items()):
        t = d['taxonomy']
        lines.append(f"| `{name}` | {d['repetitions']} | {t['total_windows']}/{t['failed_windows']} | {t['total_requests']}/{t['failed_requests']} | {t['error_rate_pct']} | {t['timeout_failures']} | {t['unknown_failures']} |")
    lines += ['', '### Confirmed points (no inferred safe operating ranges)', '']
    for name, d in sorted(failure_diagnoses.items()):
        env = d['viable_envelope']
        points = ', '.join(f"{p['offered_rps']} RPS / {p['elapsed_seconds']:.1f}s / p99 {p['p99_ms']:.2f}ms" for p in env['confirmed_points'])
        lines.append(f"- `{name}`: {points or 'unconfirmed'}; root cause unresolved.")
        for rep in d['per_repetition']:
            lines.append(f"  - rep {rep['repetition']}: {rep['status']}; CPU interval metrics {rep['queuing']['cpu_metrics']}; missing {rep['missing']}")
    lines += ['', 'Shared startup warnings do not establish causation. Collect low-load sweeps, CPU/wait traces, SQL and GC evidence before interpreting mechanisms.', '']
    return lines


def analyze_runtime_failures(output_dir):
    root = Path(output_dir)
    checks, trial_rows, details, plan = parse_diagnostic_artifacts(root)
    profile = plan.get('profile', {})
    grouped = {}
    for r in details:
        grouped.setdefault(r['trial'].get('target', 'unknown'), []).append(r)
    diagnoses = {}
    for target, records in grouped.items():
        per_rep = []
        windows = []
        observations = []
        for r in records:
            t = r['trial']
            w = r.get('warmup', [])
            windows.extend(w)
            if t.get('status') == 'passed' and t.get('measurement'):
                observations.append(t['measurement'])
            q = analyze_concurrency_and_queuing(w, r.get('cpu_start'), r.get('cpu_end'),
                profile.get('spinel_workers', 4) if target == 'spinel' else profile.get('threads', 4), target)
            per_rep.append({'repetition': t.get('repetition'), 'status': t.get('status'),
                            'taxonomy': analyze_failure_taxonomy(w), 'queuing': q,
                            'missing': r.get('missing', [])})
        diagnoses[target] = {'target': target, 'repetitions': len(records),
            'taxonomy': analyze_failure_taxonomy(windows), 'per_repetition': per_rep,
            'viable_envelope': compute_viable_load_envelope(target,
                observations=observations, profile=profile, slo_p99_ms=profile.get('slo_p99_ms', 100))}
    return diagnoses


def build_diagnostic_report(output_dir):
    """Entrypoint to parse benchmark output and generate diagnostics.json and diagnostics.md."""
    root = Path(output_dir)
    checks, trial_rows, trials_details, plan = parse_diagnostic_artifacts(root)

    # Group trials details by target
    by_target = {}
    for d in trials_details:
        tname = d.get('trial', {}).get('target')
        if tname:
            by_target.setdefault(tname, []).append(d)

    profile = plan.get('profile', {})
    endpoints = sorted(list(dict.fromkeys(
        [t.get('endpoint') for t in trial_rows if t.get('endpoint')] or
        profile.get('endpoints', ['/articles'])
    )))

    jruby_matrices = []
    cruby_matrices = []
    for ep in endpoints:
        jruby_matrices.append(analyze_jruby_matrix(by_target, ep, profile))
        cruby_matrices.append(analyze_cruby_matrix(by_target, ep, profile))
    fixed_offered_rate = profile.get('driver') == 'k6' and not profile.get('capacity_search')
    if fixed_offered_rate:
        for matrix in jruby_matrices + cruby_matrices:
            for field in ('jruby_speedup_g', 'yjit_speedup_g', 'roundhouse_speedup'):
                if field in matrix:
                    matrix[field] = {key: None for key in matrix[field]}

    # Analyze warmup progression for all trials
    warmup_trajectories = []
    for d in trials_details:
        t = d.get('trial', {})
        windows = d.get('warmup', [])
        warmup_analysis = analyze_warmup_trajectory(windows, profile)
        warmup_trajectories.append({
            'target': t.get('target', 'unknown'),
            'endpoint': t.get('endpoint', 'unknown'),
            'repetition': t.get('repetition'),
            'status': t.get('status', 'unknown'),
            'warmup_analysis': warmup_analysis,
        })

    # Detailed runtime failure diagnosis for issues #54 and #55
    failure_diagnoses = analyze_runtime_failures(output_dir)

    data = {
        'schema_version': 2,
        'fixed_offered_rate': fixed_offered_rate,
        'profile': profile,
        'endpoints': endpoints,
        'jruby_matrices': jruby_matrices,
        'cruby_matrices': cruby_matrices,
        'warmup_trajectories': warmup_trajectories,
        'failure_diagnoses': failure_diagnoses,
    }

    md_content = generate_diagnostic_markdown(data)

    # Save artifacts
    (root / 'diagnostics.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (root / 'diagnostics.md').write_text(md_content, encoding='utf-8')

    return data

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='Benchmark output directory containing raw artifacts')
    parser.add_argument('--diagnose-failures', action='store_true', help='Perform detailed failure and queueing analysis for Issues #54 & #55')
    parser.add_argument('--report-file', help='Optional path to write the diagnostic markdown report to')
    args = parser.parse_args()

    data = build_diagnostic_report(args.output)
    if args.report_file:
        Path(args.report_file).write_text(Path(args.output, 'diagnostics.md').read_text(encoding='utf-8'), encoding='utf-8')
        print(f"Report written to {args.report_file}")
    print(f"Diagnostic report generated in {args.output}/diagnostics.md and diagnostics.json")

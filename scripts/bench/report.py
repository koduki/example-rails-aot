#!/usr/bin/env python3
"""Pairwise statistical report generator from raw benchmark artifacts.

Reconstructs latency percentiles, throughput, resource consumption, and speedup ratios
(Roundhouse effect, YJIT speedup G, and interaction ratio) strictly from raw trial records.
"""
import argparse
import csv
import io
import json
import math
import statistics
from pathlib import Path

DEFAULT_SLO_P99_MS = 100.0
DEFAULT_SLO_ERROR_RATE = 0.001  # < 0.1%

def parse_trials(root_path):
    """Load preflight and trial records from an output directory."""
    root = Path(root_path)
    preflight_file = root / 'preflight/preflight.json'
    if not preflight_file.exists():
        preflight_file = root / 'preflight.json'

    checks = json.loads(preflight_file.read_text(encoding='utf-8')) if preflight_file.exists() else {}

    trials_file = root / 'trials/per-run.json'
    if not trials_file.exists():
        trials_file = root / 'per-run.json'

    trial_rows = json.loads(trials_file.read_text(encoding='utf-8')) if trials_file.exists() else []

    # Also load individual trial details if available
    trials_dir = root / 'trials'
    if trials_dir.is_dir():
        for d in sorted(trials_dir.iterdir()):
            if d.is_dir():
                trial_json = d / 'trial.json'
                if trial_json.exists():
                    detail = json.loads(trial_json.read_text(encoding='utf-8'))
                    # Match by target, endpoint, repetition
                    for r in trial_rows:
                        if (r.get('target') == detail.get('target') and
                            r.get('endpoint') == detail.get('endpoint') and
                            r.get('repetition') == detail.get('repetition')):
                            r.setdefault('detail', detail)

    return checks, trial_rows

def summarize_series(values):
    """Compute median, min, max, and IQR for a numerical series."""
    if not values:
        return {'median': None, 'min': None, 'max': None, 'iqr': None}
    s = sorted(values)
    med = statistics.median(s)
    q1 = s[len(s) // 4]
    q3 = s[(len(s) * 3) // 4]
    return {
        'median': round(med, 2),
        'min': round(min(s), 2),
        'max': round(max(s), 2),
        'iqr': round(q3 - q1, 2),
    }

def operation_metrics(measurement):
    """Return the unit of work actually offered to k6 for a CRUD trial."""
    operations = measurement.get('operations', {})
    latency = measurement.get('operation_latency_ms', {})
    if (measurement.get('driver') != 'k6-crud' or
            operations.get('ops_successful_rate') is None or
            not all(latency.get(k) is not None for k in ('p50', 'p95')) or
            not isinstance(latency.get('p99'), (int, float)) or latency['p99'] <= 0):
        return None
    return operations['ops_successful_rate'], latency, operations

def aggregate_target_endpoint(trials, target, endpoint, checks, slo_p99_ms, slo_error_rate,
                              required_repetitions=1, crud_workload=False):
    """Aggregate metrics across valid repetitions for a single target and endpoint."""
    target_checks = checks.get(target, {})
    eligible_endpoints = target_checks.get('eligible_endpoints', [])
    is_eligible = endpoint in eligible_endpoints

    matching = [t for t in trials if t.get('target') == target and t.get('endpoint') == endpoint]

    valid_repetitions = []
    excluded_repetitions = []
    verified_repetitions = []

    for t in matching:
        status = t.get('status')
        measurement = t.get('measurement', {})
        reason = t.get('reason')

        if not is_eligible:
            excluded_repetitions.append({'repetition': t.get('repetition'), 'reason': 'Ineligible endpoint (preflight failed)'})
            continue

        if status != 'passed':
            if status == 'verified':
                verified_repetitions.append(t)
            excluded_repetitions.append({'repetition': t.get('repetition'),
                'reason': reason or ('Functional smoke only; no convergence claim' if status == 'verified' else status)})
            continue

        # Check client saturation
        if measurement.get('client_saturated') or measurement.get('iterations_dropped', 0) > 0:
            excluded_repetitions.append({'repetition': t.get('repetition'), 'reason': 'Client saturated (dropped iterations)'})
            continue

        if crud_workload and operation_metrics(measurement) is None:
            excluded_repetitions.append({'repetition': t.get('repetition'),
                'reason': 'Missing CRUD operation rate or latency; HTTP request metrics are not operation metrics'})
            continue

        valid_repetitions.append(t)

    # Extract metrics from valid repetitions
    rps_list = []
    p50_list = []
    p95_list = []
    p99_list = []
    error_rate_list = []
    cpu_list = []
    container_mb_list = []

    for t in valid_repetitions:
        m = t.get('measurement', {})
        operation = operation_metrics(m) if crud_workload else None
        rps = operation[0] if operation else (m.get('rps_successful') or m.get('rps') or 0.0)
        rps_list.append(rps)

        lat = operation[1] if operation else m.get('latency_ms', {})
        p50 = lat.get('p50') or lat.get('med') or m.get('p50_ms') or 0.0
        p95 = lat.get('p95') or m.get('p95_ms') or 0.0
        p99 = lat.get('p99') or m.get('p99_ms') or 0.0
        p50_list.append(p50)
        p95_list.append(p95)
        p99_list.append(p99)

        total_reqs = operation[2].get('total', 0) if operation else (
            m.get('requests_total') or ((m.get('successful', 0) + m.get('errors', 0)) or 1))
        failed_reqs = operation[2].get('failed', 0) if operation else (
            m.get('requests_failed') if 'requests_failed' in m else m.get('errors', 0))
        err_rate = failed_reqs / total_reqs if total_reqs > 0 else 0.0
        error_rate_list.append(err_rate)

        telemetry = t.get('telemetry', {}).get('summary', {})
        if telemetry.get('mean_cpu_pct'):
            cpu_list.append(telemetry['mean_cpu_pct'])
        memory_bytes = telemetry.get('peak_container_memory_bytes')
        if memory_bytes is None:
            memory_bytes = telemetry.get('peak_rss_bytes')  # Historic docker stats artifact, previously mislabeled.
        if memory_bytes is not None:
            container_mb_list.append(memory_bytes / (1024 * 1024))

    rps_summary = summarize_series(rps_list)
    p50_summary = summarize_series(p50_list)
    p95_summary = summarize_series(p95_list)
    p99_summary = summarize_series(p99_list)
    err_summary = summarize_series(error_rate_list)
    cpu_summary = summarize_series(cpu_list)
    memory_summary = summarize_series(container_mb_list)

    # Fail if any repetition violates SLO; a median must not hide an outlier.
    per_repetition_slo = [
        {'repetition': trial['repetition'], 'passed': p99 <= slo_p99_ms and err < slo_error_rate,
         'p99_ms': p99, 'error_rate': err}
        for trial, p99, err in zip(valid_repetitions, p99_list, error_rate_list)
    ]
    slo_met = (len(per_repetition_slo) >= required_repetitions and
               all(item['passed'] for item in per_repetition_slo))

    return {
        'target': target,
        'endpoint': endpoint,
        'is_eligible': is_eligible,
        'trial_count': len(matching),
        'metric_basis': 'operation' if crud_workload else 'http_request',
        'valid_repetition_count': len(valid_repetitions),
        'required_repetition_count': required_repetitions,
        'complete': len(valid_repetitions) >= required_repetitions,
        'verified_repetition_count': len(verified_repetitions),
        'excluded_repetition_count': len(excluded_repetitions),
        'excluded_reasons': excluded_repetitions,
        'slo_met': slo_met,
        'per_repetition_slo': per_repetition_slo,
        'worst_p99_ms': max(p99_list) if p99_list else None,
        'worst_error_rate': max(error_rate_list) if error_rate_list else None,
        'rps': rps_summary,
        'p50_ms': p50_summary,
        'p95_ms': p95_summary,
        'p99_ms': p99_summary,
        'error_rate': err_summary,
        'mean_cpu_pct': cpu_summary,
        'peak_container_memory_mb': memory_summary,
    }

def compute_ratio(numerator_val, denominator_val):
    """Compute ratio between two metric values, safely returning None if missing or zero."""
    if numerator_val is None or denominator_val is None:
        return None
    if denominator_val <= 0:
        return None
    return round(numerator_val / denominator_val, 3)

def compute_pairwise_comparisons(aggregates, endpoint):
    """Calculate Roundhouse speedup, YJIT speedup G, interaction ratio, and JRuby diagnostic."""
    by_target = {a['target']: a for a in aggregates if a['endpoint'] == endpoint}

    def get_rps(target_name):
        agg = by_target.get(target_name)
        if not agg or not agg['is_eligible'] or not agg.get('complete', agg['valid_repetition_count'] > 0):
            return None
        return agg['rps']['median']

    r_cruby_off = get_rps('rails-cruby-off')
    r_cruby_yjit = get_rps('rails-cruby-yjit')
    e_cruby_off = get_rps('emit-cruby-off')
    e_cruby_yjit = get_rps('emit-cruby-yjit')
    r_jruby = get_rps('rails-jruby')
    e_jruby = get_rps('emit-jruby')
    r_jruby_off = get_rps('rails-jruby-off')
    e_jruby_off = get_rps('emit-jruby-off')
    s_spinel = get_rps('spinel')

    # Roundhouse speedup factor: capacity_emitted / capacity_Rails
    speedup_cruby_off = compute_ratio(e_cruby_off, r_cruby_off)
    speedup_cruby_yjit = compute_ratio(e_cruby_yjit, r_cruby_yjit)
    speedup_jruby = compute_ratio(e_jruby, r_jruby)
    speedup_jruby_off = compute_ratio(e_jruby_off, r_jruby_off)

    # YJIT speedup factor G = RPS_ON / RPS_OFF
    g_rails = compute_ratio(r_cruby_yjit, r_cruby_off)
    g_emitted = compute_ratio(e_cruby_yjit, e_cruby_off)

    # Interaction ratio: G_emitted / G_Rails
    interaction_ratio = compute_ratio(g_emitted, g_rails)

    # JRuby compile mode speedup: JIT / OFF
    jruby_jit_speedup_rails = compute_ratio(r_jruby, r_jruby_off)
    jruby_jit_speedup_emitted = compute_ratio(e_jruby, e_jruby_off)
    jruby_interaction_ratio = compute_ratio(jruby_jit_speedup_emitted, jruby_jit_speedup_rails)

    # Spinel comparative position (whole-system difference: HTTP server, DB adapter, native AOT)
    spinel_vs_rails_cruby_off = compute_ratio(s_spinel, r_cruby_off)
    spinel_vs_rails_cruby_yjit = compute_ratio(s_spinel, r_cruby_yjit)

    return {
        'endpoint': endpoint,
        'roundhouse_speedup': {
            'cruby_off': speedup_cruby_off,
            'cruby_yjit': speedup_cruby_yjit,
            'jruby_jit': speedup_jruby,
            'jruby_off': speedup_jruby_off,
        },
        'yjit_speedup_g': {
            'rails': g_rails,
            'emitted': g_emitted,
            'interaction_ratio': interaction_ratio,
        },
        'jruby_compile_mode_ratio': {
            'rails': jruby_jit_speedup_rails,
            'emitted': jruby_jit_speedup_emitted,
            'interaction_ratio': jruby_interaction_ratio,
        },
        'spinel_system_comparison': {
            'note': 'Full architectural difference (Spinel AOT + embedded HTTP/DB vs Puma + CRuby)',
            'ratio_vs_rails_cruby_off': spinel_vs_rails_cruby_off,
            'ratio_vs_rails_cruby_yjit': spinel_vs_rails_cruby_yjit,
        }
    }

def generate_markdown_report(report_data):
    """Format report into GitHub Flavored Markdown."""
    lines = []
    lines.append('# Benchmark P1 Pairwise Comparison Report\n')
    lines.append('> [!NOTE]')
    lines.append('> Metrics represent **medians across valid repetitions** under the recorded workload.')
    crud_workload = report_data.get('metric_basis') == 'operation'
    if report_data.get('fixed_offered_rate'):
        rate_unit = 'operation rate' if crud_workload else 'RPS'
        lines.append(f'> Fixed offered {rate_unit} measures latency, error rate and resources at that load; it does not establish maximum capacity or JIT throughput speedups.')
    else:
        lines.append('> Closed-loop throughput ratios are pilot observations, not sustained capacity estimates.')
    lines.append('> The median of per-run p99s reflects run-to-run consistency and is not a pooling of all requests into a single distribution.')
    lines.append('> Spinel comparison reflects the total architectural execution stack difference (AOT binary, server, adapter).\n')
    if crud_workload:
        lines.append('> CRUD rate and latency refer to complete logical operations (including form fetch and write requests); HTTP request metrics remain in raw JSON.\n')
    rate_heading = 'Operations/s' if crud_workload else 'RPS'
    latency_prefix = 'Op ' if crud_workload else ''

    # Target summaries table
    lines.append('## 1. Target Endpoint Performance Summary\n')
    lines.append(f'| Target | Endpoint | Status | Valid Reps | Median {rate_heading} | Median {latency_prefix}p50 (ms) | Median {latency_prefix}p95 (ms) | Median {latency_prefix}p99 (ms) | Median Err % | Peak container memory (MB) | Mean CPU % |')
    lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')

    for item in report_data['target_aggregates']:
        target = item['target']
        endpoint = item['endpoint']
        if item['is_eligible'] and item.get('complete', item['valid_repetition_count'] > 0):
            status = '✅ passed'
        elif item['is_eligible'] and item['valid_repetition_count'] > 0:
            status = '⚠️ incomplete'
        elif item['is_eligible'] and item.get('verified_repetition_count', 0) > 0:
            status = '✅ verified (functional only)'
        elif item['is_eligible'] and item.get('trial_count', 0) > 0:
            status = '⚠️ no valid trials'
        else:
            status = '❌ excluded'
        reps = f"{item['valid_repetition_count']}/{item.get('required_repetition_count', 1)}"
        rps = f"{item['rps']['median']}" if item['rps']['median'] is not None else '-'
        p50 = f"{item['p50_ms']['median']}" if item['p50_ms']['median'] is not None else '-'
        p95 = f"{item['p95_ms']['median']}" if item['p95_ms']['median'] is not None else '-'
        p99 = f"{item['p99_ms']['median']}" if item['p99_ms']['median'] is not None else '-'
        err = f"{item['error_rate']['median'] * 100:.2f}%" if item['error_rate']['median'] is not None else '-'
        rss = f"{item['peak_container_memory_mb']['median']}" if item['peak_container_memory_mb']['median'] is not None else '-'
        cpu = f"{item['mean_cpu_pct']['median']}%" if item['mean_cpu_pct']['median'] is not None else '-'

        lines.append(f"| `{target}` | `{endpoint}` | {status} | {reps} | {rps} | {p50} | {p95} | {p99} | {err} | {rss} | {cpu} |")

    lines.append('\n## 2. Pairwise Ratios and JIT Interaction\n')

    for comp in report_data['pairwise_comparisons']:
        ep = comp['endpoint']
        lines.append(f'### Workload: `{ep}`\n')
        lines.append('| Comparison Metric | Ratio | Formula / Description |')
        lines.append('| --- | --- | --- |')

        rh = comp['roundhouse_speedup']
        lines.append(f"| Roundhouse RPS Ratio (CRuby JIT Off) | **{rh['cruby_off'] or 'N/A'}** | $RPS_{{emit}} / RPS_{{rails}}$ |")
        lines.append(f"| Roundhouse RPS Ratio (CRuby YJIT) | **{rh['cruby_yjit'] or 'N/A'}** | $RPS_{{emit}} / RPS_{{rails}}$ |")
        lines.append(f"| Roundhouse RPS Ratio (JRuby compile.mode=JIT) | **{rh['jruby_jit'] or 'N/A'}** | $RPS_{{emit}} / RPS_{{rails}}$ |")
        lines.append(f"| Roundhouse RPS Ratio (JRuby compile.mode=OFF) | **{rh['jruby_off'] or 'N/A'}** | $RPS_{{emit}} / RPS_{{rails}}$ (JVM JIT active) |")

        yj = comp['yjit_speedup_g']
        lines.append(f"| Rails YJIT RPS Ratio ($G_{{Rails}}$) | **{yj['rails'] or 'N/A'}** | $RPS_{{YJIT}} / RPS_{{OFF}}$ |")
        lines.append(f"| Emitted YJIT RPS Ratio ($G_{{emitted}}$) | **{yj['emitted'] or 'N/A'}** | $RPS_{{YJIT}} / RPS_{{OFF}}$ |")
        lines.append(f"| **YJIT Interaction Ratio** | **{yj['interaction_ratio'] or 'N/A'}** | $G_{{emitted}} / G_{{Rails}}$ |")

        jr = comp['jruby_compile_mode_ratio']
        lines.append(f"| JRuby Compile Mode RPS Ratio (Rails) | **{jr['rails'] or 'N/A'}** | $RPS_{{compile.mode=JIT}} / RPS_{{compile.mode=OFF}}$ |")
        lines.append(f"| JRuby Compile Mode RPS Ratio (Emitted) | **{jr['emitted'] or 'N/A'}** | $RPS_{{compile.mode=JIT}} / RPS_{{compile.mode=OFF}}$ |")
        lines.append(f"| **JRuby Interaction Ratio** | **{jr.get('interaction_ratio') or 'N/A'}** | $G_{{JRuby,emitted}} / G_{{JRuby,Rails}}$ |")

        sp = comp['spinel_system_comparison']
        lines.append(f"| Spinel vs Rails CRuby Off | **{sp['ratio_vs_rails_cruby_off'] or 'N/A'}** | {sp['note']} |")
        lines.append(f"| Spinel vs Rails CRuby YJIT | **{sp['ratio_vs_rails_cruby_yjit'] or 'N/A'}** | {sp['note']} |")
        lines.append('')

    # Trial dispositions and execution details
    if report_data.get('trials'):
        checks = report_data.get('checks', {})
        lines.append('## 3. Individual Trial Dispositions and Execution Details\n')
        lines.append(f'| Target | Endpoint | Rep | Status | Reason / Details | {rate_heading} | {latency_prefix}p50 (ms) | {latency_prefix}p95 (ms) | {latency_prefix}p99 (ms) | Err % | Peak container memory (MB) | CPU % |')
        lines.append('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
        for t in report_data['trials']:
            tgt = t.get('target', '-')
            ep = t.get('endpoint', '-')
            rep = t.get('repetition', 1)
            raw_st = t.get('status', 'unknown')
            m = t.get('measurement', {})
            
            # Determine effective disposition and reason
            is_elig = ep in checks.get(tgt, {}).get('eligible_endpoints', []) if checks else True
            reason = t.get('reason')
            if not is_elig:
                eff_st = 'excluded'
                reason = reason or 'Ineligible endpoint (preflight failed)'
            elif raw_st != 'passed':
                eff_st = raw_st
                reason = reason or ('Functional smoke only; no convergence claim' if raw_st == 'verified'
                                    else 'Warmup unstable' if raw_st == 'unstable' else raw_st)
            elif m.get('client_saturated') or m.get('iterations_dropped', 0) > 0:
                eff_st = 'excluded'
                reason = 'Client saturated (dropped iterations)'
            else:
                eff_st = 'passed'
                reason = '-'

            badge = ('✅ passed' if eff_st == 'passed' else '✅ verified' if eff_st == 'verified'
                     else '⚠️ unstable' if eff_st == 'unstable' else f'❌ {eff_st}')
            operation = operation_metrics(m) if crud_workload else None
            lat = operation[1] if operation else m.get('latency_ms', {})
            rps_val = operation[0] if operation else (None if crud_workload else m.get('rps_successful') or m.get('rps'))
            rps = f"{rps_val:.2f}" if rps_val is not None else '-'
            p50_val = lat.get('p50') or lat.get('med') or m.get('p50_ms')
            p50 = f"{p50_val:.2f}" if p50_val is not None else '-'
            p95_val = lat.get('p95') or m.get('p95_ms')
            p95 = f"{p95_val:.2f}" if p95_val is not None else '-'
            p99_val = lat.get('p99') or m.get('p99_ms')
            p99 = f"{p99_val:.2f}" if p99_val is not None else '-'
            tot = operation[2].get('total', 0) if operation else (
                m.get('requests_total') or ((m.get('successful', 0) + m.get('errors', 0)) or 1))
            err_cnt = operation[2].get('failed', 0) if operation else (
                m.get('requests_failed') if 'requests_failed' in m else m.get('errors', 0))
            err_str = f"{(err_cnt / tot) * 100:.2f}%" if tot > 0 and (operation or
                not crud_workload and ('errors' in m or 'requests_failed' in m)) else '-'
            telemetry = t.get('telemetry', {}).get('summary', {})
            memory_bytes = telemetry.get('peak_container_memory_bytes')
            if memory_bytes is None:
                memory_bytes = telemetry.get('peak_rss_bytes')
            rss = f"{memory_bytes / (1024 * 1024):.2f}" if memory_bytes is not None else '-'
            cpu = f"{telemetry['mean_cpu_pct']:.2f}%" if telemetry.get('mean_cpu_pct') else '-'
            if eff_st == 'verified' or crud_workload and operation is None:
                # Keep raw smoke metrics in JSON, but do not invite latency or
                # throughput comparisons from short, unconverged CI trials.
                rps = p50 = p95 = p99 = rss = cpu = '-'
            lines.append(f"| `{tgt}` | `{ep}` | {rep} | {badge} | {reason} | {rps} | {p50} | {p95} | {p99} | {err_str} | {rss} | {cpu} |")
        lines.append('')

    return '\n'.join(lines) + '\n'

def generate_csv_report(report_data):
    """Format target summaries into CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)
    rate_name = 'operations_per_second' if report_data.get('metric_basis') == 'operation' else 'rps'
    writer.writerow([
        'target', 'endpoint', 'metric_basis', 'is_eligible', 'valid_repetition_count',
        f'{rate_name}_median', f'{rate_name}_min', f'{rate_name}_max', f'{rate_name}_iqr',
        'p50_ms_median', 'p95_ms_median', 'p99_ms_median',
        'error_rate_median', 'peak_container_memory_mb_median', 'mean_cpu_pct_median', 'slo_met'
    ])
    for item in report_data['target_aggregates']:
        writer.writerow([
            item['target'],
            item['endpoint'],
            item.get('metric_basis', 'http_request'),
            item['is_eligible'],
            item['valid_repetition_count'],
            item['rps']['median'],
            item['rps']['min'],
            item['rps']['max'],
            item['rps']['iqr'],
            item['p50_ms']['median'],
            item['p95_ms']['median'],
            item['p99_ms']['median'],
            item['error_rate']['median'],
            item['peak_container_memory_mb']['median'],
            item['mean_cpu_pct']['median'],
            item['slo_met'],
        ])
    return output.getvalue()

def build_report(output_dir, slo_p99_ms=DEFAULT_SLO_P99_MS, slo_error_rate=DEFAULT_SLO_ERROR_RATE):
    """Main entrypoint to generate JSON, CSV, and Markdown report from raw artifacts."""
    root = Path(output_dir)
    checks, trials = parse_trials(root)
    plan_path = root / 'plan.json'
    profile = json.loads(plan_path.read_text(encoding='utf-8')).get('profile', {}) if plan_path.exists() else {}
    fixed_offered_rate = profile.get('driver') == 'k6'
    crud_workload = profile.get('k6_script') == 'bench/k6/crud.js'

    # Determine targets and endpoints
    targets = sorted({t['target'] for t in trials}) if trials else sorted(checks.keys())
    endpoints = sorted({t['endpoint'] for t in trials if 'endpoint' in t})
    if not endpoints:
        # Fall back to endpoints from preflight if trials is empty
        endpoints = sorted(set.union(*(set(v.get('eligible_endpoints', [])) for v in checks.values()))) if checks else []

    target_aggregates = []
    for target in targets:
        for endpoint in endpoints:
            agg = aggregate_target_endpoint(trials, target, endpoint, checks, slo_p99_ms,
                                            slo_error_rate, profile.get('repetitions', 1),
                                            crud_workload=crud_workload)
            target_aggregates.append(agg)

    pairwise_comparisons = []
    for endpoint in endpoints:
        if not fixed_offered_rate:
            pairwise_comparisons.append(compute_pairwise_comparisons(target_aggregates, endpoint))

    report_data = {
        'schema_version': 1,
        'fixed_offered_rate': fixed_offered_rate,
        'metric_basis': 'operation' if crud_workload else 'http_request',
        'profile': profile,
        'slo_criteria': {'p99_ms': slo_p99_ms, 'error_rate': slo_error_rate},
        'target_aggregates': target_aggregates,
        'pairwise_comparisons': pairwise_comparisons,
        'trials': trials,
        'checks': checks,
    }

    md_content = generate_markdown_report(report_data)
    csv_content = generate_csv_report(report_data)

    # Save to output directory
    (root / 'summary.json').write_text(json.dumps(report_data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (root / 'summary.md').write_text(md_content, encoding='utf-8')
    (root / 'summary.csv').write_text(csv_content, encoding='utf-8')
    # Generate diagnostic report if diagnostic artifacts exist
    import diagnostic as p1_diag
    p1_diag.build_diagnostic_report(root)

    # Generate auxiliary evaluation report (startup, build, 4-core feasibility)
    import auxiliary as p2_aux
    p2_aux.build_auxiliary_report(run_dir=root, output_path=root)

    return report_data

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='Benchmark output directory containing raw artifacts')
    parser.add_argument('--slo-p99', type=float, default=DEFAULT_SLO_P99_MS, help='SLO target p99 latency in ms')
    parser.add_argument('--slo-error-rate', type=float, default=DEFAULT_SLO_ERROR_RATE, help='SLO target max error rate')
    args = parser.parse_args()

    data = build_report(args.output, args.slo_p99, args.slo_error_rate)
    print(f"Report generated successfully in {args.output}")
    print(f"Artifacts: summary.json, summary.csv, summary.md")

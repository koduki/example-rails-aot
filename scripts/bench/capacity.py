"""Bounded capacity search with explicit SLO and client validity decisions."""
import statistics


def decision(measurement, profile):
    total = measurement.get('requests_total', 0)
    failed = measurement.get('requests_failed', 0)
    if not total or measurement.get('client_saturated') or measurement.get('iterations_dropped', 0):
        return 'invalid_client'
    if profile.get('remote_loadgen') and measurement.get('tester_cpu_pct') is None:
        return 'invalid_client'
    if (measurement.get('tester_cpu_pct') or 0) >= profile.get('max_tester_cpu_pct', 85):
        return 'invalid_client'
    offered = measurement.get('rate_offered', 0)
    if offered and measurement.get('rps_successful', 0) < 0.95 * offered:
        return 'invalid_client'
    if failed / total >= profile['max_error_rate'] or measurement['latency_ms']['p99'] > profile['slo_p99_ms']:
        return 'slo_fail'
    return 'pass'


def search(measure, profile):
    """measure(rate, duration, phase) -> k6 summary. Confirm the final candidate."""
    steps = []
    def probe(rate, duration, phase):
        m = measure(rate, duration, phase)
        state = decision(m, profile)
        steps.append({'phase': phase, 'rate': rate, 'decision': state, 'measurement': m})
        if state == 'invalid_client':
            raise RuntimeError(f'Client saturation at {rate} RPS invalidates capacity search')
        return state
    low, high = 0, None
    rate = profile['capacity_start_rps']
    while rate <= profile['capacity_max_rps']:
        state = probe(rate, profile['capacity_step_seconds'], 'coarse')
        if state == 'slo_fail':
            high = rate
            break
        low = rate
        rate *= 2
    if low == 0:
        raise RuntimeError('No sustainable starting rate; lower capacity_start_rps')
    if high is not None:
        while high - low > profile['capacity_tolerance_rps']:
            mid = (low + high) // 2
            if probe(mid, profile['capacity_step_seconds'], 'bracket') == 'pass':
                low = mid
            else:
                high = mid
    # A short pass is not a sustainable result without a full confirmation.
    measured = measure(low, profile['measurement_seconds'], 'confirm')
    state = decision(measured, profile)
    steps.append({'phase': 'confirm', 'rate': low, 'decision': state, 'measurement': measured})
    return {'capacity_rps': low if state == 'pass' else None,
            'measurement': measured, 'steps': steps, 'status': state}


def paired_ratios(trials, numerator, denominator):
    """Pair only confirmed, valid capacities from the same repetition."""
    by_key = {(t['target'], t['repetition']): t for t in trials}
    pairs = []
    for (target, rep), row in by_key.items():
        if target != numerator or row.get('status') != 'passed':
            continue
        other = by_key.get((denominator, rep), {})
        a, b = row.get('capacity_rps'), other.get('capacity_rps')
        if other.get('status') == 'passed' and a and b:
            pairs.append({'repetition': rep, 'ratio': a / b})
    values = sorted(p['ratio'] for p in pairs)
    return {'pairs': sorted(pairs, key=lambda p: p['repetition']),
            'median': statistics.median(values) if values else None,
            'min': values[0] if values else None, 'max': values[-1] if values else None,
            'iqr': (values[3 * (len(values)-1) // 4] - values[(len(values)-1) // 4]) if values else None}

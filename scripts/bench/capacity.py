"""Bounded capacity search with explicit SLO and client validity decisions."""
import statistics
import math


def classify(measurement, profile):
    """Classify measurement into:
    - 'slo_pass': meets all latency, error rate, throughput, client, and integrity SLOs
    - 'sut_slo_fail': SUT failed SLO (latency, errors, timeouts, or integrity)
    - 'loadgen_invalid': tester deficiency (high tester CPU >= max, network errors, misconfigured loadgen)
    - 'transport_or_artifact_error': artifact transport / transfer failure
    - 'unclassified': cannot be classified
    """
    total = measurement.get('requests_total', 0)
    failed = measurement.get('requests_failed', 0)
    latency = measurement.get('latency_ms') or {}
    p99 = latency.get('p99')
    tester_cpu = measurement.get('tester_cpu_pct')
    net_errors = measurement.get('tester_network_errors', 0)
    max_tester_cpu = profile.get('max_tester_cpu_pct', 85)
    offered = measurement.get('rate_offered', 0)
    rps_succ = measurement.get('rps_successful', 0)
    dropped = measurement.get('iterations_dropped', 0)
    saturated = measurement.get('client_saturated', False)
    integrity_errors = measurement.get('integrity_errors', 0)
    verified = measurement.get('response_integrity_verified', True)

    if measurement.get('transport_error'):
        return {'category': 'transport_or_artifact_error', 'reason': 'Remote artifact transfer failure'}

    if not isinstance(p99, (int, float)) or not math.isfinite(p99) or (total > 0 and p99 <= 0):
        return {'category': 'unclassified', 'reason': 'Missing/invalid p99 latency evidence'}

    # 1. Tester / loadgen errors:
    if net_errors > 0:
        return {'category': 'loadgen_invalid', 'reason': f'Tester network errors: {net_errors}'}
    if tester_cpu is not None and tester_cpu >= max_tester_cpu:
        return {'category': 'loadgen_invalid', 'reason': f'Tester CPU exceeded limit: {tester_cpu}% >= {max_tester_cpu}%'}
    if profile.get('remote_loadgen') and tester_cpu is None:
        return {'category': 'loadgen_invalid', 'reason': 'Missing remote tester CPU telemetry'}
    if not total and (saturated or dropped > 0):
        return {'category': 'loadgen_invalid', 'reason': 'No requests executed due to load generator failure'}

    # 2. Response integrity failure (e.g. truncated body, missing articles with HTTP 200):
    if integrity_errors > 0 or not verified:
        return {'category': 'sut_slo_fail', 'reason': f'Response integrity violation: {integrity_errors} corrupted/incomplete responses'}

    # 3. Direct SUT SLO violations:
    if total > 0 and failed / total >= profile['max_error_rate']:
        return {'category': 'sut_slo_fail', 'reason': f'Error rate exceeded: {failed}/{total} >= {profile["max_error_rate"]}'}
    if p99 is not None and p99 > profile['slo_p99_ms']:
        return {'category': 'sut_slo_fail', 'reason': f'p99 latency exceeded SLO: {p99} ms > {profile["slo_p99_ms"]} ms'}

    # 4. Client saturation / dropped iterations / throughput deficit:
    # Incomplete issuance alone cannot distinguish SUT delay from client limits.
    if saturated or dropped > 0 or (offered and rps_succ < 0.95 * offered):
        return {'category': 'loadgen_invalid', 'reason': 'Incomplete issuance or VU saturation; cause unresolved without server/client traces'}

    if not total:
        return {'category': 'unclassified', 'reason': 'Zero total requests with no error indicators'}

    return {'category': 'slo_pass', 'reason': 'SLO and client requirements met'}


def decision(measurement, profile):
    info = classify(measurement, profile)
    cat = info['category']
    if cat == 'slo_pass':
        return 'pass'
    elif cat == 'sut_slo_fail':
        return 'slo_fail'
    elif cat == 'loadgen_invalid':
        return 'invalid_client'
    elif cat == 'transport_or_artifact_error':
        return 'transport_error'
    return 'unclassified'


def search(measure, profile):
    """Screen, confirm, then refine a bounded interval; retain every probe."""
    steps = []
    minimum = profile.get('capacity_min_rps', profile['capacity_start_rps'])
    maximum = profile['capacity_max_rps']
    start = profile['capacity_start_rps']
    confirmed = {}
    high = None
    high_seconds = None
    max_steps = profile.get('capacity_max_steps', 80)

    def tolerance(low):
        absolute = profile['capacity_tolerance_rps']
        relative = profile.get('capacity_tolerance_ratio')
        return min(absolute, low * relative) if relative else absolute

    def abort(message):
        error = RuntimeError(message)
        error.steps = list(steps)
        raise error

    def probe(rate, duration, phase):
        if len(steps) >= max_steps:
            abort('Capacity search budget exhausted; retain confirmed lower bounds in steps')
        rate = round(rate, 3)
        try:
            m = measure(rate, duration, phase)
        except Exception as error:
            error.steps = list(steps)
            raise
        info = classify(m, profile)
        state = decision(m, profile)
        steps.append({'phase': phase, 'rate': rate, 'duration_seconds': duration,
                      'decision': state, 'classification': info['category'],
                      'reason': info['reason'], 'measurement': m})
        if state in ('invalid_client', 'transport_error', 'unclassified'):
            abort(f'Client saturation or invalid measurement at {rate} RPS: {info["reason"]}')
        return state, m

    # Short probes locate a candidate. Invalid clients never become SUT bounds.
    low = None
    rate = start
    while rate <= maximum:
        state, m = probe(rate, profile['capacity_step_seconds'], 'coarse')
        if state != 'pass':
            high, high_seconds = rate, profile['capacity_step_seconds']
            break
        low = rate
        if rate == maximum:
            break
        rate = min(maximum, rate * 2)

    if low is None:
        rate = max(minimum, start / 2)
        while True:
            state, m = probe(rate, profile['capacity_step_seconds'], 'downward')
            if state == 'pass':
                low = rate
                break
            high, high_seconds = rate, profile['capacity_step_seconds']
            if rate == minimum:
                abort('No sustainable starting rate; lower capacity_min_rps')
            rate = max(minimum, round(rate / 2, 3))

    if high is not None:
        while high - low > tolerance(low):
            mid = round((low + high) / 2, 3)
            if mid in (low, high):
                break
            state, m = probe(mid, profile['capacity_step_seconds'], 'bracket')
            if state == 'pass':
                low = mid
            else:
                high, high_seconds = mid, profile['capacity_step_seconds']

    # A failed sustained candidate tightens the upper bound. Halving only
    # establishes a lower point; it must not terminate refinement.
    candidate = low
    while not confirmed:
        state, measured = probe(candidate, profile['measurement_seconds'], 'confirm')
        if state == 'pass':
            confirmed[candidate] = measured
            break
        high, high_seconds = candidate, profile['measurement_seconds']
        if candidate == minimum:
            return {'offered_rps': candidate, 'capacity_rps': None,
                    'steps': steps, 'measurement': measured, 'status': 'slo_fail',
                    'classification': 'sut_slo_fail', 'confirmed_lower_rps': None,
                    'failed_upper_rps': high, 'failed_upper_seconds': high_seconds}
        candidate = max(minimum, round(candidate / 2, 3))

    low = max(confirmed)
    if high is not None:
        while high - low > tolerance(low):
            mid = round((low + high) / 2, 3)
            if mid in (low, high):
                break
            state, measured = probe(mid, profile['measurement_seconds'], 'confirm')
            if state == 'pass':
                low = mid
                confirmed[low] = measured
            else:
                high, high_seconds = mid, profile['measurement_seconds']

    # End with the chosen point so its telemetry and measurement refer to
    # the same interval. Reversal on this repeat is visible, never hidden.
    if steps[-1]['rate'] != low or steps[-1]['decision'] != 'pass':
        state, measured = probe(low, profile['measurement_seconds'], 'confirm')
        if state != 'pass':
            return {'offered_rps': low, 'capacity_rps': None, 'steps': steps,
                    'measurement': measured, 'status': 'boundary_unstable',
                    'classification': 'unclassified', 'confirmed_lower_rps': low,
                    'failed_upper_rps': high, 'failed_upper_seconds': high_seconds}
        confirmed[low] = measured
    measured = confirmed[low]
    return {'offered_rps': low, 'capacity_rps': measured.get('rps_successful') if high is not None else None,
            'confirmed_successful_rps': measured.get('rps_successful'),
            'confirmed_lower_rps': low, 'failed_upper_rps': high,
            'failed_upper_seconds': high_seconds,
            'resolution_rps': high - low if high is not None else None,
            'tolerance_rps': tolerance(low),
            'steps': steps, 'measurement': measured,
            'status': 'pass' if high is not None else 'upper_bound_not_found',
            'classification': 'slo_pass' if high is not None else 'unclassified'}


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

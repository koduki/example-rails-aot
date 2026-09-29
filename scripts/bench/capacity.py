"""Bounded capacity search with explicit SLO and client validity decisions."""
import statistics


def classify(measurement, profile):
    """Classify measurement into:
    - 'slo_pass': meets all latency, error rate, throughput, client, and integrity SLOs
    - 'sut_slo_fail': SUT failed SLO (latency, errors, timeouts, or SUT saturation causing client queue exhaustion)
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
    # Distinguish whether caused by SUT delay vs under-provisioned load generator.
    if saturated or dropped > 0 or (offered and rps_succ < 0.95 * offered):
        # If server was slow (p99 > SLO / 2) or had failed requests:
        # SUT couldn't respond in time, causing k6 virtual users to accumulate and saturate.
        if (p99 is not None and p99 >= 0.5 * profile['slo_p99_ms']) or failed > 0:
            return {'category': 'sut_slo_fail', 'reason': f'SUT saturation caused client queue exhaustion (p99={p99}ms, failed={failed})'}
        # SUT responded with low latency and 0 errors, but client still saturated / dropped:
        return {'category': 'loadgen_invalid', 'reason': 'Load generator dropped iterations or saturated VUs despite low server latency'}

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
    """measure(rate, duration, phase) -> k6 summary. Confirm the final candidate."""
    steps = []
    min_rps = profile.get('capacity_min_rps')
    start_rps = profile['capacity_start_rps']

    def probe(rate, duration, phase):
        m = measure(rate, duration, phase)
        cls_info = classify(m, profile)
        state = decision(m, profile)
        steps.append({'phase': phase, 'rate': rate, 'decision': state,
                      'classification': cls_info['category'], 'reason': cls_info['reason'],
                      'measurement': m})
        if cls_info['category'] == 'loadgen_invalid':
            # Load generator / tester failed: cannot infer server capacity from a broken tester!
            if phase == 'coarse' and rate == start_rps and min_rps and min_rps < rate:
                return state
            err = RuntimeError(f'Client saturation at {rate} RPS invalidates capacity search: {cls_info["reason"]}')
            err.steps = list(steps)
            raise err
        return state

    low, high = 0, None
    rate = start_rps
    while rate <= profile['capacity_max_rps']:
        state = probe(rate, profile['capacity_step_seconds'], 'coarse')
        if state == 'pass':
            low = rate
            rate *= 2
        else:
            high = rate
            break

    if high is None and low < profile['capacity_max_rps']:
        cap = profile['capacity_max_rps']
        if probe(cap, profile['capacity_step_seconds'], 'upper-bound') == 'pass':
            low = cap
        else:
            high = cap

    # If the starting rate failed, search downward
    if low == 0 and min_rps and min_rps < start_rps:
        down_rate = start_rps // 2
        while down_rate >= min_rps:
            state = probe(down_rate, profile['capacity_step_seconds'], 'downward')
            if state == 'pass':
                low = down_rate
                break
            else:
                high = down_rate
                if state == 'invalid_client' and down_rate <= min_rps:
                    err = RuntimeError(f'Client saturation at {down_rate} RPS invalidates capacity search')
                    err.steps = list(steps)
                    raise err
                down_rate //= 2

    if low == 0:
        err = RuntimeError('No sustainable starting rate; lower capacity_start_rps')
        err.steps = list(steps)
        raise err

    # Bracket search between low and high
    if high is not None and low > 0:
        while high - low > profile['capacity_tolerance_rps']:
            mid = (low + high) // 2
            if probe(mid, profile['capacity_step_seconds'], 'bracket') == 'pass':
                low = mid
            else:
                high = mid

    if high is None:
        return {'offered_rps': low, 'capacity_rps': None, 'steps': steps,
                'measurement': steps[-1]['measurement'], 'status': 'upper_bound_not_found',
                'classification': 'unclassified'}

    # Confirmation with downward re-search on confirmation failure:
    min_cand = profile.get('capacity_min_rps') or profile.get('capacity_tolerance_rps', 25)
    tol = profile.get('capacity_tolerance_rps', 25)
    candidate = low

    while candidate >= min_cand:
        measured = measure(candidate, profile['measurement_seconds'], 'confirm')
        state = decision(measured, profile)
        cls_info = classify(measured, profile)
        steps.append({'phase': 'confirm', 'rate': candidate, 'decision': state,
                      'classification': cls_info['category'], 'reason': cls_info['reason'],
                      'measurement': measured})
        if state == 'pass':
            return {'offered_rps': candidate, 'capacity_rps': measured.get('rps_successful'),
                    'measurement': measured, 'steps': steps, 'status': 'pass',
                    'classification': 'slo_pass'}

        # Candidate failed sustained 120s confirmation! Re-search lower candidates.
        next_cand = None
        if candidate // 2 >= min_cand:
            next_cand = candidate // 2
        elif candidate - tol >= min_cand:
            next_cand = candidate - tol

        if next_cand is None or next_cand >= candidate:
            break

        # Probe the lower candidate before running 120s confirmation
        probe_state = probe(next_cand, profile['capacity_step_seconds'], 'downward')
        if probe_state != 'pass':
            candidate = next_cand
            continue
        candidate = next_cand

    last_step = steps[-1]
    return {'offered_rps': low, 'capacity_rps': None, 'steps': steps,
            'measurement': last_step['measurement'], 'status': last_step['decision'],
            'classification': last_step.get('classification', 'sut_slo_fail')}


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

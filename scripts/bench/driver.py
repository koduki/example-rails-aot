#!/usr/bin/env python3
"""Bounded closed-loop HTTP driver for P0 orchestration validation, not k6 replacement."""
import argparse
import concurrent.futures
import http.client
import json
import os
import time
from urllib.parse import urlsplit

def sample(url, duration, connections, timeout):
    parsed = urlsplit(url)
    deadline = time.monotonic() + duration
    start = time.monotonic()
    def worker(_):
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
        latencies, errors = [], 0
        try:
            while time.monotonic() < deadline:
                before = time.monotonic()
                try:
                    conn.request('GET', parsed.path)
                    r = conn.getresponse(); body = r.read()
                    if r.status != 200 or not body:
                        errors += 1
                    else:
                        latencies.append((time.monotonic() - before) * 1000)
                except (OSError, http.client.HTTPException):
                    errors += 1; conn.close()
        finally:
            conn.close()
        return latencies, errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=connections) as executor:
        results = list(executor.map(worker, range(connections)))
    values = sorted(v for row, _ in results for v in row)
    elapsed = time.monotonic() - start
    rps = len(values) / elapsed if elapsed > 0 else 0.0
    p50 = values[int(len(values)*0.50)] if values else None
    p95 = values[min(len(values)-1, int(len(values)*0.95))] if values else None
    p99 = values[min(len(values)-1, int(len(values)*0.99))] if values else None
    errors = sum(e for _, e in results)
    cpu_ids = sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else []
    return {
        'elapsed': elapsed, 'successful': len(values), 'errors': errors,
        'rps': rps, 'rps_successful': rps,
        'p50_ms': p50, 'p95_ms': p95, 'p99_ms': p99,
        'latency_ms': {'med': p50, 'p50': p50, 'p95': p95, 'p99': p99, 'min': values[0] if values else None, 'max': values[-1] if values else None},
        'requests_total': len(values) + errors, 'requests_failed': errors,
        'iterations_dropped': 0, 'client_saturated': False,
        'driver': 'p0-closed-loop', 'cpu_ids': cpu_ids
    }

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('url'); p.add_argument('--duration', type=float, required=True)
    p.add_argument('--connections', type=int, required=True); p.add_argument('--timeout', type=float, required=True)
    p.add_argument('--cpus', required=True)
    args = vars(p.parse_args())
    cpus_val = args.pop('cpus')
    if hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, {int(c) for c in cpus_val.split(',')})
    print(json.dumps(sample(**args)))

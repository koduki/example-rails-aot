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
    return {'elapsed': elapsed, 'successful': len(values), 'errors': sum(e for _, e in results),
            'rps': len(values) / elapsed,
            'p95_ms': values[min(len(values)-1, int(len(values)*0.95))] if values else None,
            'driver': 'p0-closed-loop', 'cpu_ids': sorted(os.sched_getaffinity(0))}

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('url'); p.add_argument('--duration', type=float, required=True)
    p.add_argument('--connections', type=int, required=True); p.add_argument('--timeout', type=float, required=True)
    p.add_argument('--cpus', required=True)
    args = vars(p.parse_args()); os.sched_setaffinity(0, {int(c) for c in args.pop('cpus').split(',')})
    print(json.dumps(sample(**args)))

#!/usr/bin/env python3
"""Continuous container resource telemetry: CPU, memory, cgroup peaks, and throttling."""
import argparse
import json
import re
import subprocess
import threading
import time
from pathlib import Path

def parse_bytes(val_str):
    """Parse docker byte strings like '120.5MiB', '1.2GiB', '500kB', '0B' into integer bytes."""
    if not val_str:
        return 0
    val_str = val_str.strip().replace(',', '')
    match = re.match(r'^([0-9.]+)\s*([a-zA-Z]*)$', val_str)
    if not match:
        return 0
    num = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        'b': 1,
        'k': 1000, 'kb': 1000, 'kib': 1024,
        'm': 1000**2, 'mb': 1000**2, 'mib': 1024**2,
        'g': 1000**3, 'gb': 1000**3, 'gib': 1024**3,
        't': 1000**4, 'tb': 1000**4, 'tib': 1024**4,
    }
    return int(num * multipliers.get(unit, 1))

def parse_percent(pct_str):
    """Parse percentage string like '25.30%' into float 25.30."""
    if not pct_str:
        return 0.0
    return float(pct_str.strip().replace('%', ''))

def sample_container(container_name):
    """Capture a single instantaneous docker stats snapshot for the container."""
    try:
        proc = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format', '{{json .}}', container_name],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        raw = json.loads(proc.stdout.strip())
        mem_usage_parts = raw.get('MemUsage', '').split('/')
        container_memory_bytes = parse_bytes(mem_usage_parts[0]) if mem_usage_parts else 0
        limit_bytes = parse_bytes(mem_usage_parts[1]) if len(mem_usage_parts) > 1 else 0

        return {
            'timestamp': time.time(),
            'cpu_pct': parse_percent(raw.get('CPUPerc', '0%')),
            'container_memory_bytes': container_memory_bytes,
            'limit_bytes': limit_bytes,
            'mem_pct': parse_percent(raw.get('MemPerc', '0%')),
            'pids': int(raw.get('PIDs', 0) or 0),
            'net_io': raw.get('NetIO', ''),
            'block_io': raw.get('BlockIO', ''),
        }
    except Exception:
        return None

def inspect_container(container_name):
    """Inspect final container state including OOM status and exit code."""
    try:
        proc = subprocess.run(
            ['docker', 'inspect', '--format', '{{json .}}', container_name],
            capture_output=True, text=True, timeout=5
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        info = json.loads(proc.stdout.strip())
        state = info.get('State', {})
        return {
            'oom_killed': state.get('OOMKilled', False),
            'exit_code': state.get('ExitCode', 0),
            'error': state.get('Error', ''),
            'finished_at': state.get('FinishedAt', ''),
        }
    except Exception:
        return {}

def sample_cgroup(container_name):
    """Read container cgroup v2 metrics if available."""
    metrics = {}
    for filename in ('memory.current', 'memory.peak', 'cpu.stat'):
        try:
            proc = subprocess.run(
                ['docker', 'exec', container_name, 'cat', f'/sys/fs/cgroup/{filename}'],
                capture_output=True, text=True, timeout=3
            )
            if proc.returncode == 0:
                metrics[filename] = proc.stdout.strip()
        except Exception:
            pass
    return metrics

class ResourceCollector:
    """Background sampler for container resource metrics."""
    def __init__(self, container_name, interval=1.0):
        self.container_name = container_name
        self.interval = interval
        self.samples = []
        self._stop_event = threading.Event()
        self._thread = None

    def _loop(self):
        while not self._stop_event.is_set():
            sample = sample_container(self.container_name)
            if sample:
                self.samples.append(sample)
            self._stop_event.wait(self.interval)

    def start(self):
        self._stop_event.clear()
        self.samples = []
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread:
            self._stop_event.set()
            self._thread.join(timeout=5)
            self._thread = None

        inspect = inspect_container(self.container_name)
        cgroup = sample_cgroup(self.container_name)

        cpu_values = [s['cpu_pct'] for s in self.samples]
        memory_values = [s['container_memory_bytes'] for s in self.samples]

        cgroup_peak = int(cgroup['memory.peak']) if 'memory.peak' in cgroup and cgroup['memory.peak'].isdigit() else None

        # Parse cpu.stat if available
        throttled_periods = None
        throttled_time_usec = None
        if 'cpu.stat' in cgroup:
            for line in cgroup['cpu.stat'].splitlines():
                if line.startswith('nr_throttled '):
                    throttled_periods = int(line.split()[1])
                elif line.startswith('throttled_usec '):
                    throttled_time_usec = int(line.split()[1])

        summary = {
            'container': self.container_name,
            'sample_count': len(self.samples),
            'mean_cpu_pct': round(sum(cpu_values) / len(cpu_values), 2) if cpu_values else 0.0,
            'peak_cpu_pct': round(max(cpu_values), 2) if cpu_values else 0.0,
            'mean_container_memory_bytes': int(sum(memory_values) / len(memory_values)) if memory_values else None,
            'peak_container_memory_bytes': max(memory_values) if memory_values else None,
            'peak_rss_bytes': None,
            'rss_unavailable_reason': 'Process RSS is not measured by docker stats MemUsage',
            'cgroup_peak_bytes': cgroup_peak,
            'throttled_periods': throttled_periods,
            'throttled_time_usec': throttled_time_usec,
            'oom_killed': inspect.get('oom_killed', False),
            'exit_code': inspect.get('exit_code', 0),
        }

        return {
            'summary': summary,
            'samples': self.samples,
            'cgroup': cgroup,
            'inspect': inspect,
        }

def collect_duration(container_name, duration, interval=1.0):
    """Run collector synchronously for fixed duration."""
    collector = ResourceCollector(container_name, interval=interval)
    collector.start()
    time.sleep(duration)
    return collector.stop()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('container')
    parser.add_argument('--duration', type=float, default=10.0)
    parser.add_argument('--interval', type=float, default=1.0)
    parser.add_argument('--output', help='Save telemetry JSON to file')
    args = parser.parse_args()

    result = collect_duration(args.container, args.duration, args.interval)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps(result['summary'], indent=2))

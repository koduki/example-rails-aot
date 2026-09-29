"""Local and private GCE load generation. The controller runs on the app VM."""
import json
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path


def remote_command(instance, zone, project, command):
    return ['gcloud', 'compute', 'ssh', instance, '--internal-ip',
            f'--zone={zone}', f'--project={project}', '--quiet', '--command', command]


def remote_copy(instance, zone, project, source, destination, inbound=False):
    src, dst = (f'{instance}:{source}', destination) if inbound else (source, f'{instance}:{destination}')
    return ['gcloud', 'compute', 'scp', '--internal-ip', f'--zone={zone}',
            f'--project={project}', '--quiet', src, dst]


def network_errors(text):
    """Sum NIC receive/transmit errors and drops from /proc/net/dev."""
    total = 0
    for line in text.splitlines():
        if ':' not in line:
            continue
        name, metrics = line.split(':', 1)
        if name.strip() == 'lo':
            continue
        fields = metrics.split()
        if len(fields) >= 16:
            total += sum(int(fields[i]) for i in (2, 3, 10, 11))
    return total


class RemoteLoadGenerator:
    """k6 always runs on the tester; transfer every artifact into the app trial directory."""
    def __init__(self, instance, zone, project):
        if not all((instance, zone, project)):
            raise ValueError('Remote tester instance, zone and project are required')
        self.instance, self.zone, self.project = instance, zone, project

    def probe(self):
        query = 'k6 version; uname -a; lscpu; cat /sys/fs/cgroup/cgroup.controllers; free -b'
        result = subprocess.run(remote_command(self.instance, self.zone, self.project, query),
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90)
        if result.returncode:
            raise RuntimeError('Tester environment probe failed: ' + result.stdout[-1000:])
        return result.stdout

    def run(self, script, env, directory, timeout):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        remote = '/tmp/bench-' + uuid.uuid4().hex
        def invoke(argv, limit):
            return subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=limit)
        setup = invoke(remote_command(self.instance, self.zone, self.project,
                                      'mkdir -m 700 ' + shlex.quote(remote)), 90)
        if setup.returncode:
            raise RuntimeError('Remote setup failed: ' + setup.stdout[-1000:])
        try:
            copied = invoke(remote_copy(self.instance, self.zone, self.project,
                                        str(script), remote + '/script.js'), 90)
            if copied.returncode:
                raise RuntimeError('Remote script copy failed: ' + copied.stdout[-1000:])
            environment = [item for key, value in env.items() for item in ('-e', f'{key}={value}')]
            argv = ['k6', 'run', remote + '/script.js', *environment,
                    '-e', 'SUMMARY_PATH=' + remote + '/k6-summary.json']
            # Collect tester telemetry independently of application container metrics.
            remote_shell = ('set -u; cd ' + shlex.quote(remote) + '; '
                'mpstat 1 > tester-cpu.log 2>&1 & monitor=$!; '
                'cat /proc/net/dev > network-before.txt; '
                'free -b > memory-before.txt; '
                + shlex.join(argv) + ' > k6.log 2>&1; status=$?; '
                'cat /proc/net/dev > network-after.txt; free -b > memory-after.txt; '
                'kill "$monitor" 2>/dev/null || true; exit "$status"')
            execution = invoke(remote_command(self.instance, self.zone, self.project,
                                              remote_shell), timeout + 90)
            (directory / 'remote-execution.log').write_text(execution.stdout)
            for filename in ('k6-summary.json', 'k6.log', 'tester-cpu.log',
                             'network-before.txt', 'network-after.txt',
                             'memory-before.txt', 'memory-after.txt'):
                copied = None
                for attempt in range(3):
                    copied = invoke(remote_copy(self.instance, self.zone, self.project,
                                                remote + '/' + filename,
                                                str(directory / filename), inbound=True), 90)
                    if copied.returncode == 0:
                        break
                    time.sleep(1)
                if copied.returncode and filename in ('k6-summary.json', 'k6.log'):
                    raise RuntimeError('Remote artifact transfer failed: ' + filename + ': ' + copied.stdout[-1000:])
            summary = directory / 'k6-summary.json'
            if summary.exists():
                # mpstat's per-second all-CPU rows end in the idle percentage.
                samples = []
                for line in (directory / 'tester-cpu.log').read_text().splitlines():
                    parts = line.split()
                    if 'all' in parts and len(parts) >= 10:
                        try:
                            samples.append(100 - float(parts[-1]))
                        except ValueError:
                            pass
                data = json.loads(summary.read_text())
                data['tester_cpu_pct'] = sum(samples) / len(samples) if samples else None
                before = network_errors((directory / 'network-before.txt').read_text())
                after = network_errors((directory / 'network-after.txt').read_text())
                data['tester_network_errors'] = max(0, after - before)
                summary.write_text(json.dumps(data, indent=2) + '\n')
                (directory / 'tester-telemetry.json').write_text(json.dumps({'cpu_samples_pct': samples}, indent=2) + '\n')
            if execution.returncode:
                raise RuntimeError('Remote k6 failed: ' + (directory / 'k6.log').read_text()[-1200:])
        finally:
            invoke(remote_command(self.instance, self.zone, self.project,
                                  'rm -rf -- ' + shlex.quote(remote)), 90)

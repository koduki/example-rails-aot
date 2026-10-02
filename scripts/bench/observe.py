"""Opt-in container TCP observations; socket queues are not HTTP work queues."""
import subprocess
import threading
import time

STATES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
          '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
          '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
          '0A': 'LISTEN', '0B': 'CLOSING', '0C': 'NEW_SYN_RECV'}


def parse_process(text):
    """Parse a non-atomic snapshot of PID 1; absent limits remain unknown."""
    limits, separator, descriptors = text.partition('\n__FD_TARGETS__\n')
    if not separator:
        raise ValueError('Missing FD snapshot delimiter')
    nofile = None
    for line in limits.splitlines():
        if line.startswith('Max open files'):
            fields = line.split()
            if len(fields) < 5:
                raise ValueError('Malformed nofile limits')
            nofile = {'soft': fields[3], 'hard': fields[4]}
    entries = []; counts = {}; sockets = {}
    for line in descriptors.splitlines():
        number, separator, target = line.partition('\t')
        if not separator or not number.isdigit():
            raise ValueError('Malformed FD snapshot entry')
        kind = 'socket' if target.startswith('socket:[') else 'pipe' if target.startswith('pipe:[') else \
            'anon_inode' if target.startswith('anon_inode:') else 'other'
        entries.append({'fd': int(number), 'target': target})
        counts[kind] = counts.get(kind, 0) + 1
        if kind == 'socket':
            sockets[target] = sockets.get(target, 0) + 1
    return {'pid': 1, 'limits_raw': limits, 'nofile': nofile, 'fd_count': len(entries),
            'max_fd': max((e['fd'] for e in entries), default=None), 'fd_types': counts,
            'duplicate_socket_fds': sum(n - 1 for n in sockets.values()), 'entries': entries,
            'limitation': 'Non-atomic PID 1 snapshot; duplicate socket FDs do not identify the allocating call.'}


def process_snapshot(container):
    # Inspect the serving process, not the temporary docker-exec shell's limits.
    script = '''cat /proc/1/limits || exit 1
test -r /proc/1/fd || exit 1
printf '\\n__FD_TARGETS__\\n'
find /proc/1/fd -maxdepth 1 -type l -printf '%f\\t%l\\n' '''
    started = time.time()
    result = subprocess.run(['docker', 'exec', container, 'sh', '-c', script],
                            capture_output=True, text=True, timeout=5, check=True)
    return {'started_at': started, 'finished_at': time.time(), **parse_process(result.stdout)}


def parse_tcp(text, port=3000):
    counts = {}; tx = rx = 0; listeners = []; malformed = 0
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] == 'sl':
            continue
        try:
            local_port = int(fields[1].rsplit(':', 1)[1], 16)
            if local_port != port:
                continue
            state = STATES.get(fields[3], fields[3])
            send, recv = (int(v, 16) for v in fields[4].split(':'))
        except (IndexError, ValueError):
            malformed += 1
            continue
        counts[state] = counts.get(state, 0) + 1
        if state == 'LISTEN':
            # The proc listen fields have different semantics from byte queues.
            listeners.append({'tx_queue_raw': send, 'rx_queue_raw': recv})
        else:
            tx += send; rx += recv
    return {'state_counts': counts, 'non_listen_tx_queue_bytes': tx,
            'non_listen_rx_queue_bytes': rx, 'listen_queue_raw': listeners,
            'malformed_lines': malformed}


class SocketCollector:
    def __init__(self, container, interval=2, port=3000, fd_observations=False):
        self.container, self.interval, self.port = container, interval, port
        self.samples = []; self.errors = []; self.event = threading.Event()
        self.thread = None
        self.fd_observations = fd_observations
        self.fd_samples = []; self.fd_errors = []

    def _run(self):
        while not self.event.is_set():
            started = time.time()
            try:
                result = subprocess.run(['docker', 'exec', self.container, 'cat',
                                         '/proc/net/tcp', '/proc/net/tcp6'],
                                        capture_output=True, text=True, timeout=5, check=True)
                self.samples.append({'started_at': started, 'finished_at': time.time(),
                                     'raw': result.stdout, **parse_tcp(result.stdout, self.port)})
            except (OSError, subprocess.SubprocessError) as e:
                self.errors.append({'timestamp': time.time(), 'error': str(e)})
            if self.fd_observations:
                try:
                    self.fd_samples.append(process_snapshot(self.container))
                except (OSError, ValueError, subprocess.SubprocessError) as e:
                    self.fd_errors.append({'timestamp': time.time(), 'error': str(e)})
            self.event.wait(self.interval)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.event.set()
        if self.thread:
            self.thread.join(timeout=12)
        # Copy lists so a rare late thread exit cannot mutate serialized evidence.
        result = {'schema_version': 1, 'status': 'observed' if self.samples else 'unavailable',
                'port': self.port, 'interval_seconds': self.interval,
                'sample_count': len(self.samples), 'samples': list(self.samples),
                'errors': list(self.errors), 'server_queue_drained': None,
                'limitation': 'TCP socket states/byte queues do not measure HTTP active requests, '
                              'worker occupancy, SQL wait or queue drainage.'}
        if self.fd_observations:
            result['process_fds'] = {'schema_version': 1,
                'status': 'observed' if self.fd_samples else 'unavailable',
                'sample_count': len(self.fd_samples), 'samples': list(self.fd_samples),
                'errors': list(self.fd_errors)}
        return result

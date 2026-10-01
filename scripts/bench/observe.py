"""Opt-in container TCP observations; socket queues are not HTTP work queues."""
import subprocess
import threading
import time

STATES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
          '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT',
          '07': 'CLOSE', '08': 'CLOSE_WAIT', '09': 'LAST_ACK',
          '0A': 'LISTEN', '0B': 'CLOSING', '0C': 'NEW_SYN_RECV'}


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
    def __init__(self, container, interval=2, port=3000):
        self.container, self.interval, self.port = container, interval, port
        self.samples = []; self.errors = []; self.event = threading.Event()
        self.thread = None

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
            self.event.wait(self.interval)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.event.set()
        if self.thread:
            self.thread.join(timeout=6)
        # Copy lists so a rare late thread exit cannot mutate serialized evidence.
        return {'schema_version': 1, 'status': 'observed' if self.samples else 'unavailable',
                'port': self.port, 'interval_seconds': self.interval,
                'sample_count': len(self.samples), 'samples': list(self.samples),
                'errors': list(self.errors), 'server_queue_drained': None,
                'limitation': 'TCP socket states/byte queues do not measure HTTP active requests, '
                              'worker occupancy, SQL wait or queue drainage.'}

"""Exercise a stale keep-alive socket against a real local HTTP listener."""
import socketserver
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/bench'))
import driver


class RetryServer(socketserver.TCPServer):
    allow_reuse_address = True


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(4096)
        if not self.server.dropped:
            self.server.dropped = True
            return
        self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')


class DriverUnitTests(unittest.TestCase):
    def test_idempotent_get_reconnect_is_counted_in_latency_and_telemetry(self):
        with RetryServer(('127.0.0.1', 0), Handler) as server:
            server.dropped = False
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = driver.sample(f'http://127.0.0.1:{server.server_address[1]}/articles',
                                       0.1, 1, 1)
            finally:
                server.shutdown()
                thread.join()
        self.assertGreater(result['successful'], 0)
        self.assertEqual(result['errors'], 0)
        self.assertEqual(result['transport_retries'], 1)
        self.assertEqual(result['error_types'], {})


if __name__ == '__main__':
    unittest.main()

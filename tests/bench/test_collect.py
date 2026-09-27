#!/usr/bin/env python3
"""Unit tests for container resource telemetry collector."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/bench'))
import collect

class CollectUnitTests(unittest.TestCase):
    def test_parse_bytes(self):
        self.assertEqual(collect.parse_bytes(''), 0)
        self.assertEqual(collect.parse_bytes('0B'), 0)
        self.assertEqual(collect.parse_bytes('100B'), 100)
        self.assertEqual(collect.parse_bytes('1kB'), 1000)
        self.assertEqual(collect.parse_bytes('1KiB'), 1024)
        self.assertEqual(collect.parse_bytes('10MB'), 10 * 1000 * 1000)
        self.assertEqual(collect.parse_bytes('10MiB'), 10 * 1024 * 1024)
        self.assertEqual(collect.parse_bytes('1.5GiB'), int(1.5 * 1024 * 1024 * 1024))

    def test_parse_percent(self):
        self.assertEqual(collect.parse_percent(''), 0.0)
        self.assertEqual(collect.parse_percent('0%'), 0.0)
        self.assertEqual(collect.parse_percent('25.50%'), 25.50)
        self.assertEqual(collect.parse_percent('100.0%'), 100.0)

    def test_collector_summary_aggregation(self):
        collector = collect.ResourceCollector('test-container', interval=0.01)
        mock_samples = [
            {'timestamp': 1.0, 'cpu_pct': 10.0, 'rss_bytes': 100 * 1024 * 1024},
            {'timestamp': 2.0, 'cpu_pct': 20.0, 'rss_bytes': 200 * 1024 * 1024},
            {'timestamp': 3.0, 'cpu_pct': 30.0, 'rss_bytes': 150 * 1024 * 1024},
        ]
        collector.samples = mock_samples

        mock_inspect = {'oom_killed': False, 'exit_code': 0}
        mock_cgroup = {
            'memory.peak': str(250 * 1024 * 1024),
            'cpu.stat': 'usage_usec 1000\nnr_throttled 5\nthrottled_usec 50000\n',
        }

        with patch('collect.inspect_container', return_value=mock_inspect), \
             patch('collect.sample_cgroup', return_value=mock_cgroup):
            result = collector.stop()

        summary = result['summary']
        self.assertEqual(summary['container'], 'test-container')
        self.assertEqual(summary['sample_count'], 3)
        self.assertEqual(summary['mean_cpu_pct'], 20.0)
        self.assertEqual(summary['peak_cpu_pct'], 30.0)
        self.assertEqual(summary['peak_rss_bytes'], 200 * 1024 * 1024)
        self.assertEqual(summary['cgroup_peak_bytes'], 250 * 1024 * 1024)
        self.assertEqual(summary['throttled_periods'], 5)
        self.assertEqual(summary['throttled_time_usec'], 50000)
        self.assertFalse(summary['oom_killed'])

if __name__ == '__main__':
    unittest.main()

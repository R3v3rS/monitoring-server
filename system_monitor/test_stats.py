import unittest
from unittest.mock import patch

from system_monitor import app as monitor_app


class StatsEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = monitor_app.app.test_client()
        monitor_app._prev_disk_io = None
        monitor_app._prev_net_io = None
        monitor_app._prev_ts = None

    def test_api_stats_has_required_keys(self):
        response = self.client.get('/api/stats')
        self.assertEqual(response.status_code, 200)

        payload = response.get_json()
        required_keys = {
            'cpu_percent',
            'cpu_freq_mhz',
            'ram_total_mb',
            'ram_used_mb',
            'ram_percent',
            'swap_total_mb',
            'swap_used_mb',
            'swap_percent',
            'disks',
            'disk_io',
            'net_io',
            'temperatures',
            'fans',
            'uptime_seconds',
            'top_processes',
        }
        self.assertTrue(required_keys.issubset(payload.keys()))

    def test_missing_temperature_sensor_does_not_crash(self):
        with patch('system_monitor.app.psutil.sensors_temperatures', side_effect=AttributeError):
            response = self.client.get('/api/stats')

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['temperatures'], [])

    def test_io_delta_non_negative_on_first_measurement(self):
        response = self.client.get('/api/stats')
        self.assertEqual(response.status_code, 200)

        payload = response.get_json()
        self.assertGreaterEqual(payload['disk_io']['read_mb_s'], 0)
        self.assertGreaterEqual(payload['disk_io']['write_mb_s'], 0)
        self.assertGreaterEqual(payload['net_io']['sent_mb_s'], 0)
        self.assertGreaterEqual(payload['net_io']['recv_mb_s'], 0)


if __name__ == '__main__':
    unittest.main()

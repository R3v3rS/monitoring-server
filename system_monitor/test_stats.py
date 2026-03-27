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

    def test_fan_control_invalid_channel_returns_400(self):
        response = self.client.post('/api/fans/control', json={
            'channel': 'pwm9',
            'mode': 'manual',
            'percent': 50,
        })
        self.assertEqual(response.status_code, 400)

    def test_fan_control_percent_over_100_returns_400(self):
        response = self.client.post('/api/fans/control', json={
            'channel': 'pwm1',
            'mode': 'manual',
            'percent': 101,
        })
        self.assertEqual(response.status_code, 400)

    @patch('system_monitor.app._read_fan_control_state', return_value={'mode': 'manual', 'pwm_value': 178, 'percent': 70})
    @patch('system_monitor.app._write_sysfs_int')
    def test_manual_mode_writes_enable_and_pwm(self, mock_write, _mock_state):
        response = self.client.post('/api/fans/control', json={
            'channel': 'pwm1',
            'mode': 'manual',
            'percent': 70,
        })
        self.assertEqual(response.status_code, 200)
        mock_write.assert_any_call('/sys/class/hwmon/hwmon0/pwm1_enable', 1)
        mock_write.assert_any_call('/sys/class/hwmon/hwmon0/pwm1', 178)

    @patch('system_monitor.app._write_sysfs_int')
    def test_reset_sets_auto_mode_for_all_channels(self, mock_write):
        response = self.client.post('/api/fans/reset')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            mock_write.call_args_list,
            [
                unittest.mock.call('/sys/class/hwmon/hwmon0/pwm1_enable', 5),
                unittest.mock.call('/sys/class/hwmon/hwmon0/pwm2_enable', 5),
                unittest.mock.call('/sys/class/hwmon/hwmon0/pwm3_enable', 5),
            ],
        )

    @patch('system_monitor.app._read_fan_control_state', return_value={'mode': 'manual', 'pwm_value': 64, 'percent': 25})
    @patch('system_monitor.app._write_sysfs_int')
    def test_pwm1_minimum_pwm_guard_for_zero_percent(self, mock_write, _mock_state):
        response = self.client.post('/api/fans/control', json={
            'channel': 'pwm1',
            'mode': 'manual',
            'percent': 0,
        })
        self.assertEqual(response.status_code, 200)
        mock_write.assert_any_call('/sys/class/hwmon/hwmon0/pwm1_enable', 1)
        mock_write.assert_any_call('/sys/class/hwmon/hwmon0/pwm1', 64)


if __name__ == '__main__':
    unittest.main()

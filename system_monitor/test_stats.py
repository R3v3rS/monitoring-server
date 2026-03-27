import unittest
from unittest.mock import MagicMock, patch

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

    def test_cpu_power_endpoint_has_required_keys(self):
        response = self.client.get('/api/cpu-power')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('cpu_watts', payload)
        self.assertIn('timestamp', payload)
        self.assertIn('source_available', payload)
        self.assertIn('last_error', payload)

    @patch(
        'system_monitor.app.power_monitor.get_power_snapshot',
        return_value={
            'cpu_watts': 12.5,
            'timestamp': '2026-01-01T00:00:00+00:00',
            'rolling_avg_watts': 11.3,
            'min_watts': 8.2,
            'max_watts': 15.1,
            'source_available': True,
            'last_error': None,
            'last_error_timestamp': None,
        },
    )
    def test_cpu_power_endpoint_returns_latest_snapshot(self, _mock_snapshot):
        response = self.client.get('/api/cpu-power')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['cpu_watts'], 12.5)
        self.assertEqual(payload['timestamp'], '2026-01-01T00:00:00+00:00')

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

    @patch('system_monitor.app._portfolio_repo_available', return_value=True)
    @patch('system_monitor.app.os.path.exists', return_value=True)
    def test_portfolio_backend_start_when_already_running_returns_409(self, _mock_exists, _mock_repo):
        process = MagicMock()
        process.poll.return_value = None
        monitor_app._processes['backend'] = process
        try:
            response = self.client.post('/api/portfolio/backend/start')
            self.assertEqual(response.status_code, 409)
        finally:
            monitor_app._processes['backend'] = None

    @patch('system_monitor.app._portfolio_repo_available', return_value=True)
    def test_portfolio_backend_stop_when_not_running_returns_200(self, _mock_repo):
        monitor_app._processes['backend'] = None
        response = self.client.post('/api/portfolio/backend/stop')
        self.assertEqual(response.status_code, 200)

    @patch('system_monitor.app._portfolio_repo_available', return_value=True)
    @patch('system_monitor.app._get_git_info', return_value={'uncommitted_changes': True})
    def test_portfolio_git_pull_with_uncommitted_changes_returns_409(self, _mock_git, _mock_repo):
        response = self.client.post('/api/portfolio/git/pull')
        self.assertEqual(response.status_code, 409)

    @patch('system_monitor.app._portfolio_repo_available', return_value=True)
    @patch('system_monitor.app.os.path.exists', return_value=False)
    def test_portfolio_backend_start_without_venv_returns_503(self, _mock_exists, _mock_repo):
        response = self.client.post('/api/portfolio/backend/start')
        self.assertEqual(response.status_code, 503)

    @patch('system_monitor.app._portfolio_repo_available', return_value=False)
    def test_portfolio_endpoints_without_repo_return_503(self, _mock_repo):
        response = self.client.get('/api/portfolio/status')
        self.assertEqual(response.status_code, 503)


if __name__ == '__main__':
    unittest.main()

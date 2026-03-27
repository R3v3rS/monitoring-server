import unittest

from system_monitor import power


class PowerMathTest(unittest.TestCase):
    def test_calculate_power_handles_counter_rollover(self):
        watts = power.calculate_power(
            energy_start_uj=9_500_000,
            energy_end_uj=200_000,
            elapsed_seconds=1.0,
            max_energy_range_uj=10_000_000,
        )
        self.assertAlmostEqual(watts, 0.7, places=6)

    def test_calculate_power_without_rollover_support_returns_zero_on_negative_delta(self):
        watts = power.calculate_power(
            energy_start_uj=9_500_000,
            energy_end_uj=200_000,
            elapsed_seconds=1.0,
            max_energy_range_uj=None,
        )
        self.assertEqual(watts, 0.0)


if __name__ == '__main__':
    unittest.main()

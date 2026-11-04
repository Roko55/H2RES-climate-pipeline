"""Checks tied to the actual final inputs, rather than illustrative weather."""
import contextlib
import io
import unittest
import numpy as np
from pathlib import Path
from scripts import prepared_thermal, spatial_mapping
from scripts.final_selection import select_cases
from scripts.release_runtime import accepted_array, solar_context

ROOT = Path(__file__).resolve().parents[1]


class FinalReleaseTests(unittest.TestCase):
    def test_v6_electricity_portfolio_input_scope(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cases = select_cases(ROOT, 'electricity', 2030)
        self.assertEqual(set(cases), {'low', 'avg', 'high'})
        for case in cases.values():
            self.assertEqual(case.shape, (8760, 3))
        with self.assertRaises(ValueError):
            select_cases(ROOT, 'multisector', 2030)
        with self.assertRaises(ValueError):
            select_cases(ROOT, 'electricity', 2050)

    def test_saturated_spatial_mapping_preserves_aggregate(self):
        weights = np.array([.05, .25, .7])
        target = np.array([0., .1, .9, 1.])
        raw = target[:, None]*np.array([15., 2., .1])
        mapped = spatial_mapping._balance_weighted_profiles(raw, weights, target)
        np.testing.assert_allclose(mapped@weights, target, rtol=0, atol=1e-14)
        self.assertTrue(((mapped >= 0) & (mapped <= 1)).all())

    def test_prepared_demand_restores_total_and_peak(self):
        periods = list(range(1, 8761))
        base = 2+np.sin(np.arange(8760)*2*np.pi/8760)
        demand = {('heat', p, 'zone', 'HR'): float(v) for p, v in zip(periods, base)}
        diag = prepared_thermal._reshape_prepared_temperature_demand(
            demand, periods, np.full(8760, -5.), np.full(8760, 12.), 'heat', .5, True)
        self.assertAlmostEqual(sum(demand.values()), base.sum(), places=6)
        self.assertLessEqual(max(demand.values()), base.max()+1e-10)
        self.assertEqual(diag['units_scaled'], 1)

    def test_frozen_solar_features_ignore_future_and_other_channels(self):
        _, scope = solar_context(ROOT/'models/default/solar')
        engineer = scope['ENGINE'].ChannelFeatureEngineer('Solar', np.zeros(8760))
        raw = np.random.RandomState(7).uniform(.01, .5, (900, 3))
        before_x, before_y = engineer.create_training_features(raw)
        raw[600:, 0] += 100
        raw[:, 1:] += 100
        after_x, after_y = engineer.create_training_features(raw)
        np.testing.assert_array_equal(before_x[:300], after_x[:300])
        np.testing.assert_array_equal(before_y[:300], after_y[:300])

    def test_accepted_array_identity(self):
        values = accepted_array(ROOT)
        self.assertEqual(values.dtype, np.float32)
        self.assertEqual(values.shape, (100, 8760, 3))


if __name__ == '__main__':
    unittest.main()

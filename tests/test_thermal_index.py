"""The case-selection thermal score standardizes both degree-hour channels."""
import unittest
import numpy as np
from scripts.scenario_manager import _temperature_stress_metrics


class ThermalIndexTests(unittest.TestCase):
    def test_matches_dimensionless_two_channel_definition(self):
        temperature=np.array([[5.,10.,20.,25.],[10.,15.,25.,30.],[0.,12.,22.,24.],[15.,20.,27.,35.]])
        heating=np.maximum(18-temperature,0).sum(axis=1)
        cooling=np.maximum(temperature-22,0).sum(axis=1)
        expected=(cooling-cooling.mean())/cooling.std()-(heating-heating.mean())/heating.std()
        actual=_temperature_stress_metrics(temperature)['thermal_index']
        np.testing.assert_allclose(actual,expected,rtol=0,atol=1e-14)
        self.assertAlmostEqual(float(actual.mean()),0.)

    def test_constant_ensemble_has_zero_finite_thermal_score(self):
        for temperature in (5.,20.,30.):
            actual=_temperature_stress_metrics(np.full((4,24),temperature))['thermal_index']
            np.testing.assert_array_equal(actual,np.zeros(4))


if __name__=='__main__':unittest.main()

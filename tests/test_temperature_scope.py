"""Regression checks for the country scope of temperature-demand updates."""
import unittest
from scripts.temperature_scope import reshape_demand_for_zones


class TemperatureScopeTests(unittest.TestCase):
    def test_other_countries_are_unchanged(self):
        demand={('HR1',1):10.,('HR1',2):20.,('SI1',1):30.,('BA1',1):40.}
        def transform(subset):
            for key in subset:subset[key]*=2
            return {'units_scaled':1}
        result=reshape_demand_for_zones(demand,{'HR1'},transform)
        self.assertEqual(result,{'units_scaled':1})
        self.assertEqual(demand,{('HR1',1):20.,('HR1',2):40.,('SI1',1):30.,('BA1',1):40.})

    def test_missing_scope_or_changed_keys_does_not_mutate_input(self):
        demand={('HR1',1):10.,('SI1',1):30.}
        before=dict(demand)
        with self.assertRaises(ValueError):reshape_demand_for_zones(demand,[],lambda v:None)
        def invalid(subset):subset[('HR1',2)]=20.
        with self.assertRaises(ValueError):reshape_demand_for_zones(demand,['HR1'],invalid)
        self.assertEqual(demand,before)


if __name__=='__main__':unittest.main()

"""Accounting boundaries and service deficits in the checked V6.1 snapshot."""
from pathlib import Path
import unittest
import pandas as pd
from analysis.manuscript_results import summaries
from analysis.native_results import annual_costs, operational_table
from scripts.channel_profiles import channel_cases

ROOT = Path(__file__).resolve().parents[1]


class CalibratedResultTests(unittest.TestCase):
    def test_common_historical_base_is_not_a_synthetic_future_case(self):
        with self.assertRaisesRegex(ValueError, 'historical base'):
            channel_cases(ROOT, 2020)

    def test_all_operational_cases_retained_including_three_shortfalls(self):
        frame = operational_table(ROOT)
        self.assertEqual(len(frame), 45)
        self.assertTrue(frame[frame.availability_fraction.gt(0)].HR_unserved_MWh.eq(0).all())
        short = frame[frame.HR_unserved_MWh.gt(1e-6)].set_index('portfolio')
        self.assertEqual(set(short.index), {'avg', 'low', 'high'})
        self.assertTrue(short.historical_year.eq(2021).all())
        for case, mwh, hours in [('avg', 70.40645, 1), ('low', 158.21978, 3), ('high', 65.22580, 1)]:
            self.assertAlmostEqual(short.loc[case, 'HR_unserved_MWh'], mwh, places=5)
            self.assertEqual(short.loc[case, 'HR_unserved_hours'], hours)
        zero = frame[frame.availability_fraction.eq(0)]
        self.assertTrue(zero.HR_import_MWh.eq(0).all() and zero.HR_export_MWh.eq(0).all())

    def test_existing_krsko_entitlement_is_counted_once_in_net_imports(self):
        supply = summaries(ROOT)['average_energy_supply.csv']
        first = supply.query("year == 2025 and technology == 'Net imports including Krško entitlement'")
        self.assertEqual(len(first), 1)
        self.assertAlmostEqual(first.iloc[0].TWh, 5.36944074555908, places=10)

    def test_industrial_heat_is_not_duplicated_in_cost_denominator(self):
        first = annual_costs(ROOT).query("case == 'average' and year == 2025").iloc[0]
        self.assertAlmostEqual(first.demand_MWh,
            17569133.61288213+20209008.85241509+9159870.292173693+25927.3971, places=5)

    def test_cost_ranking_and_zero_generation_additions(self):
        folder = ROOT/'results/channel_sensitivity'
        costs = pd.read_csv(folder/'objective_comparison.csv').pivot(index='year', columns='case', values='objective')
        for resource in ('solar', 'wind'):
            self.assertTrue((costs[resource+'_low'] > costs['average']).all())
            self.assertTrue((costs['average'] > costs[resource+'_high']).all())
        inv = pd.read_csv(folder/'investments_by_technology.csv')
        self.assertLess(inv[inv.family.eq('cap_inv')].value.abs().max(), 1e-7)


if __name__ == '__main__':
    unittest.main()

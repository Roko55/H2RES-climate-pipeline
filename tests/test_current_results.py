"""Contracts of the current annual accounts and their manuscript interpretation."""
from pathlib import Path
from math import fsum
import unittest
import numpy as np
from analysis.final_results import summaries,verify_results,rows,components,capacity
ROOT=Path(__file__).resolve().parents[1]
class CurrentResultTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.data=rows(ROOT);cls.frames=summaries(ROOT)
 def test_current_cohort_and_source_receipt(self):
  r=verify_results(ROOT)
  self.assertEqual(r['planning_windows'],42);self.assertEqual(r['retained_operating_cases'],45)
 def test_annual_cost_matches_reported_2025_bill(self):
  r=self.frames['annual_costs.csv'].query("case == 'average' and year == 2025").iloc[0]
  self.assertAlmostEqual(r.year_total_EUR,492745460.79883695,places=4)
  self.assertAlmostEqual(r.year_total_EUR-r.one_off_charges_EUR,492716070.79883695,places=4)
  self.assertAlmostEqual(r.EUR_per_demand_MWh,10.64,places=2)
 def test_component_differences_close_to_annual_comparison(self):
  x=self.frames['annual_cost_components.csv'].groupby(['case','year']).difference_million_EUR.sum()
  y=self.frames['annual_cost_differences.csv'].set_index(['case','year']).difference_million_EUR
  np.testing.assert_allclose(x,y.reindex(x.index),rtol=0,atol=1e-8)
 def test_reported_capacity_and_temperature_ordering(self):
  c=self.frames['cumulative_capacity.csv'].set_index('case')
  for col,target in [('solar_GW',10.71),('wind_GW',4.41),('heat_pumps_GW',3.62)]:
   self.assertEqual(round(c.loc['average',col],2),target)
  self.assertEqual(round(c.loc['temperature_cold','heat_pumps_GW'],2),4.04)
  self.assertEqual(round(c.loc['temperature_hot','heat_pumps_GW'],2),3.31)
 def test_no_new_gas_and_no_hidden_shortage(self):
  p=self.frames['policy_checks.csv']
  self.assertTrue(p.new_gas_power_MW.abs().le(1e-7).all())
  self.assertTrue(p.new_gas_boiler_MW.abs().le(1e-7).all())
  for col in ['unserved_electricity_MWh','unserved_individual_heat_MWh','unserved_district_heat_MWh']:
   self.assertTrue(p[col].abs().le(1e-7).all())
 def test_known_imports_and_temporary_solar_low_finding_retained(self):
  p=self.frames['policy_checks.csv'].set_index(['case','year'])
  self.assertAlmostEqual(p.loc['average',2050].net_import_TWh,4.34690283001963,places=9)
  self.assertGreater(p.loc['solar_low',2035].net_import_share,.33)
  self.assertLess(p.loc['solar_low',2035].net_import_share,.34)
 def test_annual_unserved_twh_is_a_sum_not_hourly_mean(self):
  o=self.frames['operating_results.csv']
  np.testing.assert_allclose(o.HR_unserved_TWh,o.HR_unserved_MWh/1e6,rtol=0,atol=0)
  x=o[o.HR_unserved_MWh.gt(1e-7)].set_index('portfolio')
  self.assertEqual(set(x.index),{'avg','low','high'})
  self.assertAlmostEqual(x.loc['low','HR_unserved_TWh'],.0001582197756,places=12)
 def test_solar_cost_order_is_not_forced_to_follow_yield(self):
  c=self.frames['annual_cost_differences.csv'].set_index(['case','year'])
  self.assertGreater(c.loc['solar_low',2035].difference_million_EUR,0)
  self.assertLess(c.loc['solar_low',2040].difference_million_EUR,0)
  for year in range(2025,2051,5):
   self.assertGreater(c.loc['wind_low',year].difference_million_EUR,0)
   self.assertLess(c.loc['wind_high',year].difference_million_EUR,0)
if __name__=='__main__':unittest.main()


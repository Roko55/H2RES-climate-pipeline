"""Verify the reported comparison scope and monetary reconciliation."""
from pathlib import Path
import unittest
import numpy as np
from analysis.manuscript_results import summaries, verify_manuscript

ROOT = Path(__file__).resolve().parents[1]


class ManuscriptResultTests(unittest.TestCase):
    def test_exact_v6_figure_and_score_inventory(self):
        evidence = verify_manuscript(ROOT)
        self.assertEqual(evidence['exact_V6_result_figures'], 15)
        self.assertEqual(evidence['mean_profile_comparisons'], 6)
        self.assertEqual(evidence['fair_CRPS_comparisons'], 3)

    def test_window_component_differences_close_to_full_objective(self):
        frames = summaries(ROOT)
        components = frames['cost_component_differences.csv'].groupby(['case', 'window_start']).difference_million_EUR.sum()
        expected = frames['window_cost_differences.csv'].set_index(['case', 'year']).difference_million_EUR
        np.testing.assert_allclose(components, expected.reindex(components.index), rtol=0, atol=1e-8)

    def test_temperature_has_both_identical_reference_markers(self):
        frame = summaries(ROOT)['mean_profile_MAE.csv'].query("channel == 'temperature'")
        self.assertEqual(set(frame.reference), {'Default H2RES profile', 'Historical mean'})
        self.assertEqual(frame.reference_MAE.nunique(), 1)
        np.testing.assert_allclose(frame.change_percent, -8.327877183388877, rtol=0, atol=1e-10)

    def test_realized_years_are_unique_and_cost_uses_same_year_demand(self):
        frame = summaries(ROOT)['realized_annual_costs.csv']
        self.assertEqual(len(frame), 49)
        self.assertFalse(frame.duplicated(['case', 'year']).any())
        first = frame.query("case == 'average' and year == 2020").iloc[0]
        # Independent manuscript source: the first anchor alone, not its two-year window.
        self.assertAlmostEqual(first.year_total_EUR, 947720046.3005062, places=4)
        self.assertAlmostEqual(first.EUR_per_demand_MWh, 17.76785101380895, places=9)


if __name__ == '__main__':
    unittest.main()

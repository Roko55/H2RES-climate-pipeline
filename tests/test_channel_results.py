"""Final single-channel inputs and accounting conventions."""
from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd
from analysis import channel_results as results
from scripts.channel_profiles import channel_cases

ROOT = Path(__file__).resolve().parents[1]


class ChannelResultTests(unittest.TestCase):
    def test_final_inputs_donors_reuse_and_accounting(self):
        report = results.verify_results(ROOT)
        self.assertEqual(report['input_profile_hashes'], 49)
        self.assertEqual(report['new_operational_tests'], 0)

    def test_provisional_investments_are_rejected(self):
        data = results.tables(ROOT)
        data['investments_by_technology'].loc[0, 'realized'] = 0
        with patch.object(results, 'tables', return_value=data):
            with self.assertRaisesRegex(ValueError, 'Provisional decisions'):
                results.verify_results(ROOT)

    def test_published_summaries_reproduce_from_delivered_tables(self):
        for name, actual in results.summaries(ROOT).items():
            published = pd.read_csv(ROOT/'results/channel_investments/summary'/name)
            pd.testing.assert_frame_equal(actual.reset_index(drop=True), published,
                                          check_dtype=False, rtol=1e-12, atol=1e-8)

    def test_unsupported_planning_anchor_is_rejected(self):
        with self.assertRaises(ValueError):
            channel_cases(ROOT, 2021)


if __name__ == '__main__':
    unittest.main()

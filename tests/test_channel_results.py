"""Final single-channel inputs and accounting conventions."""
from pathlib import Path
import unittest
import hashlib
import json
import tempfile
from unittest.mock import patch
import pandas as pd
import numpy as np
from analysis import channel_results as results
from analysis.manuscript_results import summaries
from scripts.channel_profiles import channel_cases

ROOT = Path(__file__).resolve().parents[1]


class ChannelResultTests(unittest.TestCase):
    def test_final_inputs_donors_fresh_windows_and_accounting(self):
        report = results.verify_results(ROOT)
        self.assertEqual(report['input_profile_hashes'], 42)
        self.assertEqual(report['operating_records_retained'], 45)
        self.assertEqual(report['new_windows'], 42)
        self.assertEqual(report['common_calibrated_base_year'], 2020)

    def test_stationary_weather_at_all_planning_anchors(self):
        baseline = channel_cases(ROOT, 2025)
        for year in range(2025, 2051, 5):
            for case, actual in channel_cases(ROOT, year).items():
                np.testing.assert_array_equal(actual, baseline[case])

    def test_cli_exports_match_all_42_executed_profile_hashes(self):
        import pipeline
        records = pd.read_csv(ROOT/'results/channel_sensitivity/scenario_inputs.csv')
        (ROOT/'exports').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'exports') as folder:
            for year in range(2025, 2051, 5):
                output = Path(folder)/f'cases_{year}.npz'
                pipeline.main(['select-final', '--design', 'channel', '--year', str(year),
                               '--output', str(output)])
                with np.load(output, allow_pickle=False) as archive:
                    expected = records[records.year == year].set_index('case')
                    self.assertEqual(set(archive.files), set(expected.index))
                    for case in archive.files:
                        self.assertEqual(archive[case].shape, (8760, 3))
                        self.assertEqual(hashlib.sha256(archive[case].tobytes()).hexdigest(),
                                         expected.loc[case, 'profile_sha256'])
                metadata = json.loads(output.with_suffix('.json').read_text())
                self.assertEqual(metadata['weather'], 'stationary')
                self.assertEqual(metadata['solar_factor_2050'], 1.)
                self.assertEqual(metadata['wind_factor_2050'], 1.)
                self.assertEqual(metadata['temperature_offset_2050_C'], 0.)

    def test_stationary_verifier_rejects_changing_yearly_hashes(self):
        data = results.tables(ROOT)
        data['scenario_inputs'].loc[1, 'profile_sha256'] = '0'*64
        with patch.object(results, 'tables', return_value=data):
            with self.assertRaisesRegex(ValueError, 'Weather profile changes'):
                results.verify_results(ROOT)

    def test_provisional_investments_are_rejected(self):
        data = results.tables(ROOT)
        data['investments_by_technology'].loc[0, 'realized'] = 0
        with patch.object(results, 'tables', return_value=data):
            with self.assertRaisesRegex(ValueError, 'Provisional decisions'):
                results.verify_results(ROOT)

    def test_published_summaries_reproduce_from_delivered_tables(self):
        for name, actual in summaries(ROOT).items():
            published = pd.read_csv(ROOT/'results/manuscript/tables'/name)
            pd.testing.assert_frame_equal(actual.reset_index(drop=True), published,
                                          check_dtype=False, rtol=1e-12, atol=1e-8)

    def test_unsupported_planning_anchor_is_rejected(self):
        with self.assertRaises(ValueError):
            channel_cases(ROOT, 2021)


if __name__ == '__main__':
    unittest.main()

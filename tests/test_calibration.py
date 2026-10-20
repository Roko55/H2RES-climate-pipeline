import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
import pandas as pd
from scripts.capacity_factor_calibration import fit_alpha, strict_alpha, select_targets, prepare_archive, digest
from scripts import scenario_manager as sm


class CalibrationTests(unittest.TestCase):
    def test_known_scale(self):
        self.assertAlmostEqual(fit_alpha(np.array([[.2], [.4]]), np.ones(1), .15), .5)

    def test_clipping_applies_per_site(self):
        values = np.array([[.1, .9], [.2, .8]])
        weights = np.array([.25, .75])
        alpha = fit_alpha(values, weights, .8)
        self.assertAlmostEqual(float(np.clip(values * alpha, 0, 1).mean(axis=0) @ weights), .8)

    def test_future_weather_is_not_used(self):
        years = np.array([2014, 2015, 2016, 2020])
        values = np.array([[.2], [.4], [.9], [.9]])
        expected = strict_alpha(values, np.ones(1), years, {2014: .2, 2015: .1})
        values[2:] = 0
        self.assertEqual(expected, strict_alpha(values, np.ones(1), years, {2014: .2, 2015: .1}))

    def test_future_target_rejected(self):
        with self.assertRaises(ValueError):
            strict_alpha(np.ones((2, 1)), np.ones(1), np.array([2015, 2020]), {2020: .2})

    def test_target_selection_excludes_later_years(self):
        rows = pd.DataFrame({'ISO3 code': ['HRV'] * 8,
                             'Technology': ['solar photovoltaic'] * 8,
                             'Year': list(range(2011, 2019)),
                             'Electricity Installed Capacity (MW)': [100] * 8,
                             'Electricity Generation (GWh)': [100] * 8})
        first = select_targets(rows, 'solar photovoltaic')
        rows.loc[rows.Year > 2015, 'Electricity Generation (GWh)'] = 800
        self.assertEqual(first, select_targets(rows, 'solar photovoltaic'))
        self.assertEqual(sorted(first), list(range(2011, 2016)))

    def test_invalid_calibration_input_rejected(self):
        for values, weights, target in ((np.ones(2), [1], .2), ([[.2]], [np.nan], .2),
                                       ([[.2]], [1], np.nan), ([[-.1]], [1], .2),
                                       ([[.2]], [0], .2)):
            with self.assertRaises(ValueError):
                fit_alpha(values, weights, target)

    def test_spatial_reference_excludes_later_years(self):
        first = pd.DataFrame({'zone': .2}, index=pd.date_range('2015-01-01', periods=8760, freq='h'))
        later = pd.DataFrame({'zone': .9}, index=pd.date_range('2020-01-01', periods=8784, freq='h'))
        with patch.object(sm, 'SPATIAL_REFERENCE_END_YEAR', 2015):
            expected = sm._hourly_mean_8760(first)
            actual = sm._hourly_mean_8760(pd.concat([first, later]))
            pd.testing.assert_frame_equal(expected, actual)
            with self.assertRaises(ValueError):
                sm._hourly_mean_8760(later)

    def test_archive_preparation_and_output_protection(self):
        dates = pd.date_range('1980-01-01', '2024-12-31 23:00', freq='h')
        old = pd.DataFrame({'time': dates, 'solar': .1, 'wind': .15, 'temperature': 12.})
        targets = pd.DataFrame({'ISO3 code': ['HRV', 'HRV'],
                                'Technology': ['solar photovoltaic', 'onshore wind'],
                                'Year': [2015, 2015],
                                'Electricity Installed Capacity (MW)': [100., 100.],
                                'Electricity Generation (GWh)': [175.2, 262.8]})
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source'
            source.mkdir()
            (source / 'raw_data/openmeteo_wind').mkdir(parents=True)
            pd.DataFrame([{'channel': channel, 'zone_name': zone, 'weight_value': 1.}
                          for channel in ('solar', 'temperature') for zone in ('A', 'B', 'C')]).to_csv(
                              source / 'HR_macro_climate_metadata.csv', index=False)
            pd.DataFrame([{'lat': 45., 'lon': 16., 'column': 'wind_site', 'cap_mw': 100.}]).to_csv(
                source / 'HR_wind_sites_meta.csv', index=False)
            for name in ('HR_solar_calibration_metadata.json', 'HR_calibration_metadata.json'):
                (source / name).write_text(json.dumps({'calibration_alpha': .5}), encoding='utf-8')
            for name in ('HR_data.parquet', 'IRENA_Statistics_Extract_2025H2.xlsx',
                         'raw_data/openmeteo_wind/site_45.000_16.000.parquet'):
                (source / name).write_bytes(b'fixture')
            for zone in ('A', 'B', 'C'):
                for prefix in ('solar', 'temp'):
                    (source / 'raw_data' / f'{prefix}_{zone}.parquet').write_bytes(b'fixture')
            def read(path):
                if path.name == 'HR_data.parquet':
                    return old
                if path.parent.name == 'openmeteo_wind':
                    return pd.DataFrame({'cf': .3}, index=dates)
                if path.name.startswith('solar_'):
                    return pd.DataFrame({'solar': .2}, index=dates)
                return pd.DataFrame({'temperature': 12.}, index=dates)
            output = Path(folder) / 'output'
            with patch('scripts.capacity_factor_calibration.pd.read_parquet', side_effect=read), \
                 patch('scripts.capacity_factor_calibration.pd.read_excel', return_value=targets):
                prepared = prepare_archive(source, output)
            frame = pd.read_parquet(prepared)
            np.testing.assert_allclose(frame.solar, .2, atol=1e-12)
            np.testing.assert_allclose(frame.wind, .3, atol=1e-12)
            self.assertEqual(len(frame), len(dates))
            record = json.loads((output / 'preprocessing_manifest.json').read_text())
            self.assertEqual(record['output_sha256'], digest(prepared))
            with self.assertRaises(FileExistsError):
                prepare_archive(source, output)


if __name__ == '__main__':
    unittest.main()

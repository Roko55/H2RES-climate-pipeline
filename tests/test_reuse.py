"""Cross-project contracts, explicit thermal baselines and mapping rejection."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import pipeline
import h2res_interface as interface
from examples.demo import create_inputs


class ReuseTests(unittest.TestCase):
    def test_availability_indexing_and_injection_preserve_other_units(self):
        arrays = {"my_solar": np.linspace(0, .5, 8760), "my_wind": np.full(8760, .3)}
        entries = interface.availability_parameters(arrays, 2030, expected_units=arrays)
        self.assertEqual(len(entries), 2*8760)
        self.assertEqual(entries["my_solar", 1, 2030], 0.)
        self.assertEqual(entries["my_solar", 8760, 2030], .5)
        existing = dict(entries)
        existing["other", 1, 2020] = .7
        updated = interface.inject_availability(existing, entries)
        self.assertEqual(updated["other", 1, 2020], .7)
        del existing["my_solar", 1, 2030]
        with self.assertRaises(KeyError):
            interface.inject_availability(existing, entries)

    def test_availability_rejects_name_year_shape_and_bounds_errors(self):
        with self.assertRaises(ValueError):
            interface.availability_frame({"solar": np.zeros(8760)}, 2030, ["other_name"])
        for year in (2030.5, True, -1):
            with self.assertRaises(ValueError):
                interface.availability_frame({"solar": np.zeros(8760)}, year)
        for data in (np.zeros(24), np.full(8760, np.nan), np.full(8760, 1.1)):
            with self.assertRaises(ValueError):
                interface.availability_frame({"solar": data}, 2030)

    def test_explicit_thermal_baseline_no_lookup_or_input_mutation(self):
        heat = {("my_heat", p, 2030): 10. for p in range(1, 8761)}
        cooling = {("my_cooling", p, 2030): 4. for p in range(1, 8761)}
        cop = {("my_hp", p, 2030): 3. for p in range(1, 8761)}
        temp = 12 + 10*np.sin(np.arange(8760)*2*np.pi/8760)
        with patch.object(interface.thermal, "_load_country_baseline_temp_profile",
                          side_effect=AssertionError("Implicit country lookup is forbidden")):
            result = interface.couple_temperature(temp, np.full(8760, 12.), 2030,
                heat_demand=heat, cooling_demand=cooling, hp_cop_period=cop,
                general_heat_units=["my_heat"], cooling_units=["my_cooling"], heat_pump_units=["my_hp"])
        self.assertAlmostEqual(sum(result["heat_demand"].values()), 87600., places=6)
        self.assertAlmostEqual(sum(result["cooling_demand"].values()), 35040., places=6)
        self.assertEqual(set(heat.values()), {10.})
        self.assertEqual(set(cop.values()), {3.})
        self.assertGreater(np.std(list(result["hp_cop_period"].values())), .01)

    def test_partial_or_missing_thermal_baseline_is_rejected(self):
        options = dict(heat_demand={}, cooling_demand={}, hp_cop_period={}, general_heat_units=["missing"])
        with self.assertRaises(KeyError):
            interface.couple_temperature(np.zeros(8760), np.zeros(8760), 2030, **options)
        with self.assertRaises(ValueError):
            interface.couple_temperature(np.zeros(8760), np.zeros(10), 2030, **options)

    def test_mapping_rejects_missing_units_negative_weights_and_unknown_zones(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)/"fixture"
            create_inputs(folder)
            profile = folder/"ZZ_solar_macro_profiles.parquet"
            transfer = folder/"ZZ_solar_transfer_macro_to_units.csv"
            original = pd.read_csv(transfer)
            units = {"solar_west": 30., "solar_east": 70.}
            pipeline.validate_spatial_transfer(profile, transfer, units)
            bad = original.copy()
            bad.loc[0, "weight"] = -1.
            unknown = original.copy()
            unknown.loc[0, "source_zone"] = "misspelled_zone"
            for invalid in (original.iloc[:1], bad, unknown):
                invalid.to_csv(transfer, index=False)
                with self.assertRaises(ValueError):
                    pipeline.validate_spatial_transfer(profile, transfer, units)

    def test_no_hydro_export_and_no_implicit_resolution_fallback(self):
        (pipeline.ROOT/"exports").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=pipeline.ROOT/"exports") as folder:
            folder = Path(folder)
            create_inputs(folder/"inputs")
            (folder/"inputs/ncre_aval_factor_ZZ_2020_2050_sdewes.csv").unlink()
            np.savez(folder/"case.npz", example=np.tile([.1, .3, 12.], (8760, 1)))
            args = ["export-units", "--scenarios", str(folder/"case.npz"), "--scenario", "example",
                    "--mapping-dir", str(folder/"inputs"), "--country", "ZZ", "--exclude-hror"]
            pipeline.main(args+["--wind-resolution", "macro", "--output", str(folder/"units.npz")])
            with np.load(folder/"units.npz") as archive:
                self.assertEqual(set(archive.files), {"solar_west", "solar_east", "wind_inland", "wind_coast"})
            with self.assertRaises(FileNotFoundError):
                pipeline.main(args+["--wind-resolution", "nuts3", "--output", str(folder/"nuts3.npz")])


if __name__ == "__main__":
    unittest.main()

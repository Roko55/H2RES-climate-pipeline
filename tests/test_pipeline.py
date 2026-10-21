"""Input, causality, scenario-selection and H2RES interface regression tests."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
import pipeline
from scripts import scenario_manager as sm, temperature_coupling as tc


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = pipeline.core()

    def test_future_weather_does_not_change_training_climatology(self):
        raw = np.random.default_rng(3).normal(size=(10*8760, 3))
        before = self.model.split_data(raw)
        changed = raw.copy()
        changed[8*8760:] += 1000
        after = self.model.split_data(changed)
        for key in ("train", "hourly_mean", "hourly_std"):
            np.testing.assert_array_equal(before[key], after[key])

    def test_features_use_only_own_past(self):
        rng = np.random.default_rng(9)
        raw = rng.normal(size=(900, 3))
        fe = self.model.ChannelFeatureEngineer("Solar", np.zeros(8760))
        x, y = fe.create_training_features(raw)
        future = raw.copy()
        future[600:, 0] += 1000
        future[:, 1:] += 2000
        changed_x, changed_y = fe.create_training_features(future)
        np.testing.assert_array_equal(x[:300], changed_x[:300])
        np.testing.assert_array_equal(y[:300], changed_y[:300])

    def test_wind_bootstrap_replay_and_bounds(self):
        data = np.random.default_rng(4).uniform(.01, .9, 2*8760)
        a = self.model._generate_wind_bootstrap(data, 8760, 2, seed=101)
        b = self.model._generate_wind_bootstrap(data, 8760, 2, seed=101)
        np.testing.assert_array_equal(a, b)
        self.assertEqual(a.shape, (2, 8760))
        self.assertTrue(np.isfinite(a).all() and (a >= 0).all() and (a <= 1).all())
        self.assertFalse(np.array_equal(a[0], a[1]))

    def test_source_year_validation_rejects_gaps_and_incomplete_years(self):
        time = pd.date_range("2000-01-01", "2009-12-31 23:00", freq="h")
        frame = pd.DataFrame({"time": time, "solar": .1, "wind": .2, "temperature": 12.})
        raw, years = pipeline.validated_frame(frame)
        self.assertEqual(raw.shape, (10*8760, 3))
        self.assertEqual(len(years), 10)
        for invalid in (frame.drop(index=500), frame.iloc[1:], pd.concat([frame, frame.iloc[[-1]]])):
            with self.assertRaises(ValueError):
                pipeline.validated_frame(invalid)

    def test_profiles_reject_bad_shape_nonfinite_and_bounds(self):
        for array in (np.zeros((8760, 3)), np.full((1, 8760, 3), np.nan), np.ones((1, 8760, 3))*2):
            with self.assertRaises(ValueError):
                pipeline.validate_profiles(array)

    def test_cli_rejects_unknown_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            pipeline.main(["generate", "--data", "missing", "--output", "exports/test.npy", "--num-scenarios", "1"])

    def test_native_model_data_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path/"data").write_bytes(b"not the model input")
            (path/"metadata.json").write_text(json.dumps({"data_sha256": "0"*64}))
            with self.assertRaisesRegex(ValueError, "Data hash"):
                pipeline.load_native(path, path/"data")

    def test_default_calibration_metadata_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = root/"models/default"
            bundle.mkdir(parents=True)
            metadata = bundle/"metadata.json"
            metadata.write_text('{"calibration": "original"}')
            (root/"artifact_manifest.json").write_text(json.dumps({"files": {
                "models/default/metadata.json": {"sha256": pipeline.digest(metadata)}}}))
            metadata.write_text('{"calibration": "changed"}')
            with patch.object(pipeline, "ROOT", root), self.assertRaisesRegex(ValueError, "Default model metadata"):
                pipeline.load_native(bundle, root/"unused_data")

    def test_missing_wind_pool_is_rejected(self):
        params = {"hourly_mean": np.zeros((8760, 3)), "hourly_std": np.ones((8760, 3))}
        with self.assertRaisesRegex(ValueError, "wind pool"):
            self.model.generate_scenarios({}, params, num_scenarios=1)

    def test_new_training_native_bundle_handoff(self):
        # Exercise actual small-tree fitting and native serialization. Calibration
        # is fixed here to keep the fixture independent of long annual simulation.
        raw = np.random.default_rng(23).uniform(.01, .5, (10*8760, 3))
        params = self.model.get_default_params(False)
        params.update(n_estimators=2, max_depth=2, early_stopping_rounds=1)
        calibration = {c: {"sigma_calibration": 1., "rho": .1, "drift_alpha": .2}
                       for c in ("Solar", "Temperature")}
        (pipeline.ROOT/"exports").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=pipeline.ROOT/"exports") as folder:
            folder = Path(folder)
            data = folder/"data.marker"
            data.write_bytes(b"synthetic training fixture")
            with patch.object(pipeline, "load_data", return_value=(raw, list(range(2000, 2010)))), \
                 patch.object(self.model, "get_default_params", return_value=params), \
                 patch.object(self.model, "calibrate_residuals", return_value=calibration):
                pipeline.main(["train", "--data", str(data), "--seed", "23", "--latitude", "45", "--longitude", "16",
                               "--output", str(folder/"fit")])
            models, metadata = pipeline.load_native(folder/"fit", data)
            self.assertEqual(sum(map(len, models.values())), 6)
            self.assertFalse(metadata["reporting_used_for_fit_or_calibration"])
            for channel in ("Solar", "Temperature"):
                self.assertEqual(metadata["calibration"][channel]["drift_alpha"], pipeline.DRIFT_ALPHA)
            self.assertEqual(metadata["generation_policy"]["noise_multiplier"], 1)
            self.assertEqual(metadata["split_hours"], {"train": 8*8760, "val": 8760, "test": 8760})

    def test_output_cannot_overwrite_or_escape_exports(self):
        with self.assertRaises(ValueError):
            pipeline.new_output(pipeline.ROOT/"README.md")
        (pipeline.ROOT/"exports").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=pipeline.ROOT/"exports") as folder:
            output = Path(folder)/"used.npy"
            output.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                pipeline.new_output(output)

    def test_orthogonal_selection_preserves_controlled_channels(self):
        (pipeline.ROOT/"exports").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=pipeline.ROOT/"exports") as folder:
            folder = Path(folder)
            values = np.zeros((18, 8760, 3), dtype=np.float32)
            for i in range(18):
                values[i, :, 0] = .05 + i*.005
                values[i, :, 1] = .1 + i*.01
                values[i, :, 2] = 5 + i
            np.save(folder/"input.npy", values)
            pipeline.main(["select", "--profiles", str(folder/"input.npy"), "--method", "orthogonal",
                           "--output", str(folder/"selected.npz")])
            with np.load(folder/"selected.npz") as selected:
                for resource in ("low_yield", "avg_yield", "high_yield"):
                    for thermal in ("cold", "normal", "hot"):
                        combined = selected[resource+"__"+thermal]
                        np.testing.assert_array_equal(combined[:, :2], selected[resource][:, :2])
                        np.testing.assert_array_equal(combined[:, 2], selected[thermal][:, 2])

    def test_spatial_mapping_capacity_weighted_conservation(self):
        units = {"unit_a": 1., "unit_b": 3.}
        components = {"zones": ["a", "b"],
            "zone_ratio_profiles": pd.DataFrame({"a": np.full(8760, .5), "b": np.full(8760, 7/6)}),
            "unit_zone_weights": pd.DataFrame([[1., 0.], [0., 1.]], index=list(units), columns=["a", "b"]),
            "zone_cap_share": pd.Series([.25, .75], index=["a", "b"])}
        profile = np.tile([.2, .3, 12.], (8760, 1))
        with patch.object(sm, "_get_solar_units", return_value=units), \
             patch.object(sm, "_get_wind_units", return_value=units), \
             patch.object(sm, "_load_hror_profile", return_value=np.full(8760, .1)), \
             patch.object(sm, "_load_solar_spatial_components", return_value=components), \
             patch.object(sm, "_load_wind_spatial_components", return_value=components), \
             patch.object(sm, "_resolve_wind_resolution", return_value=("nuts3", {})):
            _, diagnostic = sm.decompose_to_h2res_units(profile, return_diagnostics=True)
        self.assertLess(diagnostic["solar_mass_mean_abs_error"], 1e-12)
        self.assertLess(diagnostic["wind_mass_mean_abs_error"], 1e-12)

    def test_temperature_shape_preserves_annual_total(self):
        baseline = np.linspace(1., 5., 8760)
        shape = tc._normalized_shape(np.maximum(18 - np.linspace(-5, 30, 8760), 0))
        for blend in (0., .5, 1.):
            reshaped = tc._reshape_with_target_total(baseline, baseline.sum(), shape, blend)
            self.assertAlmostEqual(reshaped.sum(), baseline.sum(), places=8)
            self.assertTrue((reshaped >= 0).all())
        self.assertEqual(tc._annual_scale_factor(200., 100., .9, False), 1.)


if __name__ == "__main__":
    unittest.main()

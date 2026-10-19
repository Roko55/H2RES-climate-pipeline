"""Validated entry points for the H2RES climate profile pipeline.

The maintained model includes a causal-feature warmup correction; its original
execution source is retained in provenance. Scenario-selection and temperature-
coupling modules are unchanged. Inputs are validated before module calls.
"""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent
for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[name] = "1"
os.environ.setdefault("MPLBACKEND", "Agg")

import argparse
import hashlib
import json
import tempfile
import numpy as np
import pandas as pd

CHANNELS = ["solar", "wind", "temperature"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, values):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(values, stream, indent=2, allow_nan=False)


def new_output(path):
    path = Path(path).resolve()
    path.relative_to((ROOT / "exports").resolve())
    if path.exists() or path.with_suffix(".json").exists():
        raise FileExistsError("Choose a new output under exports/")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def validate_profiles(values):
    if values.ndim != 3 or values.shape[1:] != (8760, 3) or len(values) < 1:
        raise ValueError("Expected [annual_profiles, 8760, 3]")
    if not np.isfinite(values).all():
        raise ValueError("Profiles contain nonfinite values")
    if (values[..., :2] < 0).any() or (values[..., :2] > 1).any():
        raise ValueError("Solar and wind capacity factors must lie in [0, 1]")
    return values


def validated_frame(frame):
    required = ["time", *CHANNELS]
    if not set(required).issubset(frame.columns) or len(frame) == 0:
        raise ValueError("Supply nonempty time, solar, wind, temperature columns")
    dates = pd.to_datetime(frame.time)
    if dates.dt.tz is not None:
        dates = dates.dt.tz_convert("UTC").dt.tz_localize(None)
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("Timestamps must be ordered, unique and finite")
    if not (dates.diff().dropna() == pd.Timedelta(hours=1)).all():
        raise ValueError("Supply consecutive hourly source timestamps including leap days")
    if (dates.dt.minute != 0).any() or (dates.dt.second != 0).any():
        raise ValueError("Timestamps must fall on whole hours")
    kept = ~((dates.dt.month == 2) & (dates.dt.day == 29))
    clean = frame.loc[kept, required].copy()
    times = dates.loc[kept]
    years = sorted(times.dt.year.unique().tolist())
    if len(years) < 10:
        raise ValueError("At least ten complete years are required for train/validation/reporting")
    for year in years:
        part = times[times.dt.year == year]
        if len(part) != 8760 or part.iloc[0] != pd.Timestamp(year=year, month=1, day=1) or part.iloc[-1] != pd.Timestamp(year=year, month=12, day=31, hour=23):
            raise ValueError("Every source year must be complete and aligned to January 1")
    validate_profiles(clean[CHANNELS].to_numpy().reshape(-1, 8760, 3))
    return clean[CHANNELS].to_numpy(), years


def load_data(path):
    return validated_frame(pd.read_parquet(path))


def verify_sources():
    manifest = json.loads((ROOT / "provenance/source_manifest.json").read_text())
    for name, record in manifest.items():
        if digest(ROOT / name) != record["sha256"]:
            raise ValueError("Source differs from recorded implementation: " + name)


def core():
    verify_sources()
    from scripts import xgboost_climate_model
    return xgboost_climate_model


def load_native(folder, data):
    import xgboost as xgb
    metadata = json.loads((folder / "metadata.json").read_text())
    if folder.resolve() == (ROOT / "models/default").resolve():
        inventory = json.loads((ROOT / "artifact_manifest.json").read_text())
        if digest(folder / "metadata.json") != inventory["files"]["models/default/metadata.json"]["sha256"]:
            raise ValueError("Default model metadata differs from the publication record")
    if digest(data) != metadata["data_sha256"]:
        raise ValueError("Data hash differs from the fitted training/calibration record")
    if digest(ROOT / "scripts/xgboost_climate_model.py") != metadata["source_sha256"]:
        compatibility = json.loads((ROOT / "provenance/generation_compatibility.json").read_text())
        if (metadata["source_sha256"] != compatibility["training_source_sha256"] or
            digest(ROOT / "scripts/xgboost_climate_model.py") != compatibility["generation_source_sha256"] or
            digest(ROOT / "provenance/executed/xgboost_climate_model.py") != metadata["source_sha256"]):
            raise ValueError("Generator source differs from native model metadata and compatibility record")
    if xgb.__version__ != metadata["xgboost"]:
        raise ValueError("Use the recorded XGBoost version: " + metadata["xgboost"])
    models = {channel: {} for channel in ("Solar", "Temperature")}
    for entry in metadata["models"]:
        path = (folder / entry["file"]).resolve()
        path.relative_to(folder.resolve())
        if digest(path) != entry["sha256"]:
            raise ValueError("Native model hash differs: " + entry["file"])
        channel, quantile = entry["channel"], float(entry["quantile"])
        if channel not in models or quantile in models[channel]:
            raise ValueError("Invalid or duplicate native model entry")
        model = xgb.XGBRegressor()
        model.load_model(path)
        model.set_params(device="cpu", n_jobs=1)
        models[channel][quantile] = model
    if len(metadata["models"]) != 6 or any(set(v) != {.16, .5, .84} for v in models.values()):
        raise ValueError("Expected all six quantile models")
    return models, metadata


def generate(args):
    if not 1 <= args.count <= 128:
        raise ValueError("Count must be between 1 and 128")
    output = new_output(args.output)
    if output.suffix != ".npy":
        raise ValueError("Choose a .npy output")
    raw, years = load_data(args.data)
    module = core()
    models, metadata = load_native(Path(args.model_dir), args.data)
    module.CONFIG.latitude = float(metadata["latitude"])
    splits = module.split_data(raw)
    params = {name: splits[name] for name in ("hourly_mean", "hourly_std")}
    params.update(wind_train_data=splits["train"][:, 1],
                  temp_train_min=float(splits["train"][:, 2].min()),
                  temp_train_max=float(splits["train"][:, 2].max()))
    for channel in ("Solar", "Temperature"):
        calibration = metadata["calibration"][channel]
        for key, source in (("sigma_cal", "sigma_calibration"), ("rho", "rho"), ("drift_alpha", "drift_alpha")):
            params[channel.lower() + "_" + key] = calibration[source]
    values = validate_profiles(module.generate_scenarios(models, params,
        num_scenarios=args.count, history_data=splits["train"], seed=args.seed))
    with output.open("xb") as stream:
        np.save(stream, values)
    save_json(output.with_suffix(".json"), {
        "shape": list(values.shape), "channels": CHANNELS, "units": ["capacity factor", "capacity factor", "degrees C"],
        "seed": args.seed, "data_sha256": digest(args.data), "model_training_source_sha256": metadata["source_sha256"],
        "generation_source_sha256": digest(ROOT / "scripts/xgboost_climate_model.py"),
        "model_metadata_sha256": digest(Path(args.model_dir) / "metadata.json"),
        "output_sha256": digest(output), "data_years": years,
        "prediction_device": "cpu", "CPU_threads": 1,
        "evaluation_scope": "See provenance/data_provenance.json; supplied corpus is retrospective"})
    print(f"Generated {len(values)} annual profiles: {output}")


def train(args):
    import xgboost as xgb
    output = new_output(args.output)
    raw, years = load_data(args.data)
    output.mkdir(exist_ok=False)
    module = core()
    module.CONFIG.latitude = args.latitude
    np.random.seed(args.seed)
    splits = module.split_data(raw)
    params = module.get_default_params(False)
    params.update(n_jobs=1, random_state=args.seed)
    models, engineers = module.train_models(splits, use_gpu=False,
        custom_params={channel: params.copy() for channel in ("Solar", "Temperature")})
    calibration = module.calibrate_residuals(models, engineers, splits)
    entries = []
    for channel, quantiles in models.items():
        for quantile, model in quantiles.items():
            name = f"{channel.lower()}_q{int(quantile*100)}.ubj"
            model.save_model(output / name)
            entries.append({"channel": channel, "quantile": quantile, "file": name,
                            "sha256": digest(output / name), "bytes": (output / name).stat().st_size})
    save_json(output / "metadata.json", {
        "training_seed": args.seed, "xgboost": xgb.__version__, "latitude": args.latitude,
        "data_sha256": digest(args.data), "source_sha256": digest(ROOT / "scripts/xgboost_climate_model.py"),
        "models": entries, "calibration": calibration, "data_years": years,
        "split_hours": {key: len(splits[key]) for key in ("train", "val", "test")},
        "reporting_used_for_fit_or_calibration": False})
    print(f"Saved native models: {output}")


def select(args):
    values = validate_profiles(np.load(args.profiles, allow_pickle=False))
    if len(values) < 3:
        raise ValueError("At least three annual profiles are required for three clusters")
    output = new_output(args.output)
    if output.suffix != ".npz":
        raise ValueError("Choose a .npz output")
    verify_sources()
    from scripts import scenario_manager as manager
    yield_values = values[:, :, :2].sum(axis=(1, 2))
    thermal_values = manager._temperature_stress_metrics(values[:, :, 2])["thermal_index"]
    if np.unique(yield_values).size < 3 or np.unique(thermal_values).size < 3:
        raise ValueError("Three distinct renewable-yield and thermal-stress values are required")
    # Use only the caller's validated ensemble; disable the legacy auto-generation path.
    previous_dir = manager.DATA_DIR
    previous_method = os.environ.get("H2RES_COMBINED_SCENARIO_METHOD")
    try:
        with tempfile.TemporaryDirectory() as folder:
            np.save(Path(folder) / "xgb_synthetic_scenarios.npy", values)
            manager.DATA_DIR = folder
            os.environ["H2RES_COMBINED_SCENARIO_METHOD"] = args.method
            records = manager.get_representative_scenarios(year=None)
    finally:
        manager.DATA_DIR = previous_dir
        if previous_method is None:
            os.environ.pop("H2RES_COMBINED_SCENARIO_METHOD", None)
        else:
            os.environ["H2RES_COMBINED_SCENARIO_METHOD"] = previous_method
    with output.open("xb") as stream:
        np.savez_compressed(stream, **{key: record["profile"] for key, record in records.items() if key != "metadata"})
    summary = {key: {k: v for k, v in record.items() if k != "profile"}
               for key, record in records.items() if key != "metadata"}
    save_json(output.with_suffix(".json"), {"input_sha256": digest(args.profiles),
        "output_sha256": digest(output), "selection_metadata": records["metadata"], "scenarios": summary,
        "interpretation": "Designed sensitivity cases; cluster fractions are not weather-event probabilities"})


def export_units(args):
    values = np.load(args.scenarios, allow_pickle=False)
    profile = values[args.scenario]
    validate_profiles(profile[None])
    output = new_output(args.output)
    if output.suffix != ".npz":
        raise ValueError("Choose a .npz output")
    verify_sources()
    from scripts import scenario_manager as manager
    manager.DATA_DIR = str(Path(args.mapping_dir).resolve())
    for cache in (manager._solar_units_cache, manager._wind_units_cache, manager._ratio_cache):
        cache.clear()
    cc = args.country
    for name in (f"genco_data_{cc}_sdewes.csv", f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv"):
        if not (Path(args.mapping_dir) / name).is_file():
            raise FileNotFoundError("Required mapping input: " + name)
    ncre_path = Path(args.mapping_dir) / f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv"
    ncre = pd.read_csv(ncre_path)
    if "hror" not in ncre or "period" not in ncre:
        raise ValueError("Explicit period and hror columns required in the existing H2RES availability input")
    if not manager._get_solar_units(cc) or not manager._get_wind_units(cc):
        raise ValueError("Mapping requires explicit solar and wind solver units")
    if manager._load_solar_spatial_components(cc, manager._get_solar_units(cc)) is None:
        raise ValueError("Spatial solar artifacts required; legacy fallback is not an audited export")
    units, diagnostics = manager.decompose_to_h2res_units(profile, country_code=cc,
        wind_resolution_mode=args.wind_resolution, return_diagnostics=True)
    for name, array in units.items():
        if array.shape != (8760,) or not np.isfinite(array).all() or (array < 0).any() or (array > 1).any():
            raise ValueError("Invalid unit profile: " + name)
    for channel in ("solar", "wind"):
        error = diagnostics[channel + "_mass_mean_abs_error"]
        if not np.isfinite(error) or error > args.mass_tolerance:
            raise ValueError(f"{channel} capacity-weighted mapping error {error} exceeds tolerance")
    with output.open("xb") as stream:
        np.savez_compressed(stream, **units)
    used = {**manager._solar_artifact_paths(cc), **{"wind_"+k: v for k, v in manager._wind_artifact_paths(cc).items()}}
    used.update(genco=str(Path(args.mapping_dir)/f"genco_data_{cc}_sdewes.csv"), ncre=str(ncre_path))
    save_json(output.with_suffix(".json"), {"scenario": args.scenario, "diagnostics": diagnostics,
        "mapping_inputs": {Path(p).name: digest(p) for p in used.values() if Path(p).is_file()},
        "input_sha256": digest(args.scenarios), "output_sha256": digest(output)})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    gen = commands.add_parser("generate", help="Generate with hash-checked native models")
    gen.add_argument("--data", required=True)
    gen.add_argument("--model-dir", default=str(ROOT / "models/default"))
    gen.add_argument("--count", type=int, default=15)
    gen.add_argument("--seed", type=int, default=101)
    gen.add_argument("--output", required=True)
    gen.set_defaults(function=generate)
    fit = commands.add_parser("train", help="Fit six CPU quantile models and validation calibration")
    fit.add_argument("--data", required=True)
    fit.add_argument("--seed", type=int, default=42)
    fit.add_argument("--latitude", type=float, default=44.52)
    fit.add_argument("--output", required=True)
    fit.set_defaults(function=train)
    choice = commands.add_parser("select", help="Select representative annual scenarios")
    choice.add_argument("--profiles", required=True)
    choice.add_argument("--method", choices=("orthogonal", "joint_medoid"), required=True)
    choice.add_argument("--output", required=True)
    choice.set_defaults(function=select)
    export = commands.add_parser("export-units", help="Map selected profiles to H2RES unit arrays")
    export.add_argument("--scenarios", required=True)
    export.add_argument("--scenario", required=True)
    export.add_argument("--mapping-dir", required=True)
    export.add_argument("--country", default="HR")
    export.add_argument("--wind-resolution", choices=("auto", "nuts3", "macro"), default="auto")
    export.add_argument("--mass-tolerance", type=float, default=1e-4)
    export.add_argument("--output", required=True)
    export.set_defaults(function=export_units)
    args = parser.parse_args(argv)
    if hasattr(args, "country") and (len(args.country) != 2 or not args.country.isalpha() or args.country != args.country.upper()):
        parser.error("Country must be a two-letter uppercase code")
    if getattr(args, "mass_tolerance", 1e-4) <= 0 or not np.isfinite(getattr(args, "mass_tolerance", 1e-4)):
        parser.error("Mass tolerance must be positive and finite")
    args.function(args)


if __name__ == "__main__":
    main()

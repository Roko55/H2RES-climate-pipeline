"""Validated entry points for the H2RES climate profile pipeline.

The maintained model includes a causal-feature warmup correction. Optional
explicit-baseline and hydro-export extensions support other H2RES projects.
Original sources are retained in provenance. Inputs are validated before calls.
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
import sys
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


def validate_spatial_transfer(profile_path, transfer_path, units, reference_end_year=None):
    """Reject mappings that legacy loaders would repair with implicit fallbacks."""
    profiles = pd.read_parquet(profile_path)
    transfer = pd.read_csv(transfer_path)
    if not {"unit_name", "source_zone", "weight"}.issubset(transfer.columns):
        raise ValueError("Transfer CSV requires unit_name, source_zone, weight")
    if transfer[["unit_name", "source_zone"]].isna().any().any():
        raise ValueError("Transfer names must not be missing")
    for column in ("unit_name", "source_zone"):
        if transfer[column].astype(str).str.strip().eq("").any():
            raise ValueError("Transfer names must not be empty")
        transfer[column] = transfer[column].astype(str).str.strip()
    weights = pd.to_numeric(transfer.weight, errors="coerce")
    if not np.isfinite(weights).all() or (weights < 0).any():
        raise ValueError("Transfer weights must be finite and nonnegative")
    if transfer.duplicated(["unit_name", "source_zone"]).any():
        raise ValueError("Each unit/source-zone pair must have one explicit weight")
    if set(transfer.unit_name) != set(units):
        raise ValueError("Transfer unit names must exactly match the selected solver units")
    if (transfer.assign(weight=weights).groupby("unit_name").weight.sum() <= 0).any():
        raise ValueError("Every solver unit requires a positive total transfer weight")
    zones = set(transfer.source_zone)
    if not zones.issubset(profiles.columns):
        raise ValueError("Transfer references source zones missing from the profile table")
    values = profiles[sorted(zones)].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or (values > 1).any():
        raise ValueError("Spatial source capacity factors must be finite and within [0, 1]")
    if "time" in profiles:
        dates = pd.to_datetime(profiles.time)
    elif isinstance(profiles.index, pd.DatetimeIndex):
        dates = pd.Series(profiles.index)
    else:
        raise ValueError("Spatial profiles require a time column or DatetimeIndex")
    if dates.isna().any() or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("Spatial timestamps must be ordered, unique and finite")
    if dates.dt.tz is not None:
        utc = dates.dt.tz_convert("UTC").dt.tz_localize(None)
        if not utc.equals(dates.dt.tz_localize(None)):
            raise ValueError("Convert spatial timestamps to UTC before writing the input file")
        dates = utc
    if (dates.dt.minute != 0).any() or (dates.dt.second != 0).any():
        raise ValueError("Spatial timestamps must fall on whole hours")
    dates = dates.loc[~((dates.dt.month == 2) & (dates.dt.day == 29))]
    if reference_end_year is not None:
        dates = dates.loc[dates.dt.year <= reference_end_year]
    ordinal = dates.dt.dayofyear - (dates.dt.is_leap_year & (dates.dt.month > 2)).astype(int)
    hours = (ordinal-1)*24 + dates.dt.hour
    if set(hours) != set(range(8760)):
        raise ValueError("Spatial data must cover every non-leap hour of year; no gap interpolation is allowed")


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
    if folder.resolve() in {(ROOT / "models/default").resolve(), (ROOT / "models/training_20261019").resolve()}:
        inventory = json.loads((ROOT / "artifact_manifest.json").read_text())
        metadata_name = (folder.resolve() / "metadata.json").relative_to(ROOT.resolve()).as_posix()
        if digest(folder / "metadata.json") != inventory["files"][metadata_name]["sha256"]:
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
        "evaluation_scope": ("Retrospective shared corpus; see provenance/data_provenance.json"
            if digest(args.data) == "d92748ca6625d0092b073b9bc16345da960a04c42da29926da5b125a63ad122d"
            else "User-supplied corpus; upstream calibration and external validation not audited")})
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


def calibrate(args):
    from scripts.capacity_factor_calibration import prepare_archive
    output = new_output(args.output)
    path = prepare_archive(args.source_dir, output)
    print(f"Prepared training-calibrated archive: {path}")


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
    manager.SPATIAL_REFERENCE_END_YEAR = args.reference_end_year
    for cache in (manager._solar_units_cache, manager._wind_units_cache, manager._ratio_cache):
        cache.clear()
    cc = args.country
    required = [f"genco_data_{cc}_sdewes.csv"]
    if not args.exclude_hror:
        required.append(f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv")
    for name in required:
        if not (Path(args.mapping_dir) / name).is_file():
            raise FileNotFoundError("Required mapping input: " + name)
    ncre_path = Path(args.mapping_dir) / f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv"
    if not args.exclude_hror:
        ncre = pd.read_csv(ncre_path)
        if "hror" not in ncre or "period" not in ncre:
            raise ValueError("Explicit period and hror columns required in the existing H2RES availability input")
        hydro = pd.to_numeric(ncre.hror, errors="coerce")
        if not np.isfinite(hydro).all() or (hydro < 0).any() or (hydro > 1).any():
            raise ValueError("Hydro availability must be finite capacity factors in [0, 1]")
        periods = pd.to_numeric(ncre.period, errors="coerce")
        if not np.isfinite(periods).all() or (periods % 1 != 0).any() or (periods < 1).any():
            raise ValueError("Hydro period values must be positive integers")
        if set((periods.astype(int)-1) % 8760) != set(range(8760)):
            raise ValueError("Hydro availability must cover all 8760 hour-of-year periods")
    genco = pd.read_csv(Path(args.mapping_dir)/f"genco_data_{cc}_sdewes.csv")
    if not {"unit_name", "technology", "fuel_type", "cap_mw"}.issubset(genco.columns):
        raise ValueError("Unit table requires unit_name, technology, fuel_type, cap_mw")
    if genco[genco.technology == "PHOT"].empty or genco[genco.fuel_type == "Wind"].empty:
        raise ValueError("Unit table must explicitly identify PHOT solar and Wind fuel units")
    capacities = pd.to_numeric(genco.cap_mw, errors="coerce")
    if not np.isfinite(capacities).all() or (capacities < 0).any() or genco.unit_name.isna().any():
        raise ValueError("Unit names and finite nonnegative capacities are required")
    if not manager._get_solar_units(cc) or not manager._get_wind_units(cc):
        raise ValueError("Mapping requires explicit solar and wind solver units")
    solar_units, wind_units = manager._get_solar_units(cc), manager._get_wind_units(cc)
    if set(solar_units) & set(wind_units) or sum(solar_units.values()) <= 0 or sum(wind_units.values()) <= 0:
        raise ValueError("Solar/wind unit names must be disjoint and each group needs positive capacity")
    solar_paths, wind_paths = manager._solar_artifact_paths(cc), manager._wind_artifact_paths(cc)
    validate_spatial_transfer(solar_paths["macro_profiles"], solar_paths["transfer_macro"], solar_units, args.reference_end_year)
    if args.wind_resolution == "auto" and not Path(wind_paths["sites_meta"]).is_file():
        raise FileNotFoundError("Wind auto resolution requires sites metadata; otherwise choose macro or nuts3 explicitly")
    if args.wind_resolution == "nuts3" and not all(Path(wind_paths[k]).is_file() for k in ("nuts_profiles", "transfer_nuts")):
        raise FileNotFoundError("Requested NUTS3 resolution requires both NUTS3 profiles and transfer CSV")
    resolution, _ = manager._resolve_wind_resolution(cc, args.wind_resolution)
    prefix = "nuts" if resolution == "nuts3" else "macro"
    validate_spatial_transfer(wind_paths[prefix+"_profiles"], wind_paths["transfer_"+prefix], wind_units, args.reference_end_year)
    if manager._load_solar_spatial_components(cc, manager._get_solar_units(cc)) is None:
        raise ValueError("Spatial solar artifacts required; legacy fallback is not an audited export")
    units, diagnostics = manager.decompose_to_h2res_units(profile, country_code=cc,
        wind_resolution_mode=resolution, return_diagnostics=True, include_hror=not args.exclude_hror)
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
        "input_sha256": digest(args.scenarios), "output_sha256": digest(output),
        "spatial_reference_end_year": args.reference_end_year,
        "includes_hror": not args.exclude_hror})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("calibrate", help="Prepare the Croatian archive with training-period CF targets")
    preparation.add_argument("--source-dir", required=True)
    preparation.add_argument("--output", required=True)
    preparation.set_defaults(function=calibrate)
    gen = commands.add_parser("generate", help="Generate with hash-checked native models")
    gen.add_argument("--data", required=True)
    gen.add_argument("--model-dir", default=str(ROOT / "models/training_20261019"))
    gen.add_argument("--count", type=int, default=15)
    gen.add_argument("--seed", type=int, default=101)
    gen.add_argument("--output", required=True)
    gen.set_defaults(function=generate)
    fit = commands.add_parser("train", help="Fit six CPU quantile models and validation calibration")
    fit.add_argument("--data", required=True)
    fit.add_argument("--seed", type=int, default=42)
    fit.add_argument("--latitude", type=float, required=True)
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
    export.add_argument("--reference-end-year", type=int, default=2015)
    export.add_argument("--wind-resolution", choices=("auto", "nuts3", "macro"), default="auto")
    export.add_argument("--mass-tolerance", type=float, default=1e-4)
    export.add_argument("--exclude-hror", action="store_true", help="Export only solar/wind; no hydro input required")
    export.add_argument("--output", required=True)
    export.set_defaults(function=export_units)
    args = parser.parse_args(argv)
    if hasattr(args, "latitude") and (not np.isfinite(args.latitude) or not -90 <= args.latitude <= 90):
        parser.error("Latitude must be finite and between -90 and 90 degrees")
    if hasattr(args, "country") and (len(args.country) != 2 or not args.country.isalpha() or args.country != args.country.upper()):
        parser.error("Country must be a two-letter uppercase code")
    if getattr(args, "mass_tolerance", 1e-4) <= 0 or not np.isfinite(getattr(args, "mass_tolerance", 1e-4)):
        parser.error("Mass tolerance must be positive and finite")
    args.function(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, FileNotFoundError, FileExistsError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(2)

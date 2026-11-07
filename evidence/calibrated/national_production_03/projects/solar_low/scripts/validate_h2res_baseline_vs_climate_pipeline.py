"""Validate legacy H2RES climate inputs against the current climate pipeline.

This script performs a leakage-safe comparison on held-out historical years:

* legacy H2RES renewable inputs are reconstructed from the original shared
  ncre/genco files and repeated over the test years;
* the current climate pipeline is retrained on train years only, calibrated on
  validation years only, and evaluated on test years;
* the test years are never used in model fitting, calibration, or bootstrap
  sampling.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd
from scipy.stats import ks_2samp, wasserstein_distance


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from xgboost_climate_model import (  # noqa: E402
    ALL_CHANNELS,
    CHANNEL_IDX,
    HOURS_PER_YEAR,
    calibrate_residuals,
    evaluate_model,
    generate_scenarios,
    load_climate_data,
    load_models,
    split_data,
    train_models,
)


@dataclass(frozen=True)
class LegacyProfile:
    solar: np.ndarray
    wind_onshore: np.ndarray
    solar_units: List[Tuple[str, float]]
    wind_units: List[Tuple[str, float]]
    source_ncre: str
    source_genco: str


def _find_first(names: Iterable[str], suffix: str) -> str:
    matches = [name for name in names if name.endswith(suffix)]
    if not matches:
        raise FileNotFoundError(f"Could not find {suffix} inside ZIP archive.")
    return matches[0]


def _read_csv_from_zip(zip_path: Path, member_name: str) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member_name) as fh:
            return pd.read_csv(fh)


def _capacity_weighted_profile(
    ncre_df: pd.DataFrame,
    genco_df: pd.DataFrame,
    technology: str,
    exclude_name_contains: str | None = None,
) -> Tuple[np.ndarray, List[Tuple[str, float]]]:
    units = genco_df[
        (genco_df["System"].astype(str).str.upper() == "HR")
        & (genco_df["technology"].astype(str).str.upper() == technology)
        & (pd.to_numeric(genco_df["cap_mw"], errors="coerce") > 0)
    ].copy()

    if exclude_name_contains:
        mask = ~units["unit_name"].astype(str).str.contains(
            exclude_name_contains, case=False, na=False
        )
        units = units[mask]

    available_units: List[Tuple[str, float]] = []
    weighted = np.zeros(len(ncre_df), dtype=float)
    total_capacity = 0.0

    for _, row in units.iterrows():
        unit = str(row["unit_name"])
        cap = float(row["cap_mw"])
        if unit not in ncre_df.columns:
            continue
        values = pd.to_numeric(ncre_df[unit], errors="coerce").fillna(0.0).to_numpy()
        weighted += cap * values
        total_capacity += cap
        available_units.append((unit, cap))

    if total_capacity <= 0:
        raise ValueError(f"No matching HR {technology} units found in legacy ncre/genco.")

    return np.clip(weighted / total_capacity, 0.0, 1.0), available_units


def load_legacy_h2res_profiles(zip_path: Path) -> LegacyProfile:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        ncre_member = _find_first(names, "ncre_aval_factor_HR_2020_2050_sdewes7.csv")
        genco_member = _find_first(names, "genco_data_HR_sdewes18.csv")

    ncre_df = _read_csv_from_zip(zip_path, ncre_member)
    genco_df = _read_csv_from_zip(zip_path, genco_member)

    if len(ncre_df) != HOURS_PER_YEAR:
        raise ValueError(
            f"Expected legacy ncre file to contain 8760 rows, found {len(ncre_df)}."
        )

    solar, solar_units = _capacity_weighted_profile(ncre_df, genco_df, "PHOT")
    wind, wind_units = _capacity_weighted_profile(
        ncre_df, genco_df, "WTON", exclude_name_contains="Offshore"
    )

    return LegacyProfile(
        solar=solar,
        wind_onshore=wind,
        solar_units=solar_units,
        wind_units=wind_units,
        source_ncre=ncre_member,
        source_genco=genco_member,
    )


def repeat_to_length(profile: np.ndarray, target_len: int) -> np.ndarray:
    reps = int(np.ceil(target_len / len(profile)))
    return np.tile(profile, reps)[:target_len]


def metric_row(channel: str, truth: np.ndarray, old: np.ndarray, new: np.ndarray, note: str = ""):
    old_mae = float(np.mean(np.abs(old - truth)))
    new_mae = float(np.mean(np.abs(new - truth)))
    old_rmse = float(np.sqrt(np.mean((old - truth) ** 2)))
    new_rmse = float(np.sqrt(np.mean((new - truth) ** 2)))
    return {
        "channel": channel,
        "test_hours_evaluated": int(len(truth)),
        "legacy_H2RES_MAE": old_mae,
        "current_pipeline_MAE": new_mae,
        "MAE_improvement_pct": float((old_mae - new_mae) / old_mae * 100.0)
        if old_mae
        else np.nan,
        "legacy_H2RES_RMSE": old_rmse,
        "current_pipeline_RMSE": new_rmse,
        "RMSE_improvement_pct": float((old_rmse - new_rmse) / old_rmse * 100.0)
        if old_rmse
        else np.nan,
        "legacy_H2RES_bias": float(np.mean(old - truth)),
        "current_pipeline_bias": float(np.mean(new - truth)),
        "note": note,
    }


def distribution_row(channel: str, truth: np.ndarray, old: np.ndarray, new: np.ndarray):
    old_ks = float(ks_2samp(truth, old).statistic)
    new_ks = float(ks_2samp(truth, new).statistic)
    old_w = float(wasserstein_distance(truth, old))
    new_w = float(wasserstein_distance(truth, new))
    return {
        "channel": channel,
        "legacy_H2RES_KS_D": old_ks,
        "current_pipeline_KS_D": new_ks,
        "KS_improvement_pct": float((old_ks - new_ks) / old_ks * 100.0)
        if old_ks
        else np.nan,
        "legacy_H2RES_Wasserstein": old_w,
        "current_pipeline_Wasserstein": new_w,
        "Wasserstein_improvement_pct": float((old_w - new_w) / old_w * 100.0)
        if old_w
        else np.nan,
    }


def build_norm_params(splits: Dict, calibration: Dict) -> Dict:
    params: Dict[str, np.ndarray | float] = {
        "hourly_mean": splits["hourly_mean"],
        "hourly_std": splits["hourly_std"],
        "wind_train_data": splits["train"][:, CHANNEL_IDX["Wind"]],
        "temp_train_min": float(np.min(splits["train"][:, CHANNEL_IDX["Temperature"]])),
        "temp_train_max": float(np.max(splits["train"][:, CHANNEL_IDX["Temperature"]])),
    }
    for channel, cal in calibration.items():
        key = channel.lower()
        params[f"{key}_sigma_cal"] = float(cal["sigma_calibration"])
        params[f"{key}_rho"] = float(cal["rho"])
        params[f"{key}_drift_alpha"] = float(cal.get("drift_alpha", 0.0))
    return params


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--legacy-zip",
        default=str(Path.home() / "Downloads" / "wetransfer_h2res_new_share_2026-05-18_1425.zip"),
        help="Original shared H2RES ZIP used as the legacy baseline.",
    )
    parser.add_argument(
        "--num-scenarios",
        type=int,
        default=100,
        help="Number of generated scenarios for distribution validation.",
    )
    parser.add_argument(
        "--fast-calibration",
        action="store_true",
        help="Skip drift pilot generation. Use only for quick smoke tests.",
    )
    parser.add_argument(
        "--use-saved-current",
        action="store_true",
        help="Use the saved current pipeline models from ./models instead of retraining.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_DIR / "output" / "validation" / "sebastian_validation"),
    )
    args = parser.parse_args()

    legacy_zip = Path(args.legacy_zip)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not legacy_zip.exists():
        raise FileNotFoundError(f"Legacy ZIP not found: {legacy_zip}")

    print("[1/6] Loading held-out historical climate data...")
    raw_data = load_climate_data(str(PROJECT_DIR / "data" / "SC" / "HR_data.parquet"))
    splits = split_data(raw_data)
    test_data = splits["test"]
    test_truth_eval = test_data[168:]

    print("[2/6] Loading legacy H2RES renewable profiles...")
    legacy = load_legacy_h2res_profiles(legacy_zip)
    legacy_solar_test = repeat_to_length(legacy.solar, len(test_data))[168:]
    legacy_wind_test = repeat_to_length(legacy.wind_onshore, len(test_data))[168:]
    legacy_temp_test = repeat_to_length(splits["hourly_mean"][:, CHANNEL_IDX["Temperature"]], len(test_data))[168:]

    if args.use_saved_current:
        print("[3/6] Loading saved current climate pipeline models...")
        models, norm_params = load_models(str(PROJECT_DIR / "models"))
        expected_wind_len = len(splits["train"])
        actual_wind_len = len(norm_params.get("wind_train_data", []))
        if actual_wind_len != expected_wind_len:
            raise ValueError(
                "Saved wind bootstrap pool length does not match the current train split: "
                f"expected {expected_wind_len}, found {actual_wind_len}."
            )
        print("[4/6] Saved model sanity check passed: wind bootstrap pool matches train split.")
    else:
        print("[3/6] Training current climate pipeline on train years only...")
        models, feature_engineers = train_models(splits)

        print("[4/6] Calibrating current climate pipeline on validation years only...")
        calibration = calibrate_residuals(
            models, feature_engineers, splits, fast=bool(args.fast_calibration)
        )
        norm_params = build_norm_params(splits, calibration)

    print("[5/6] Evaluating point metrics on test years only...")
    # Recreate feature engineers from the saved/trained model anchors. This keeps
    # evaluation independent from any feature-engineer object persisted elsewhere.
    from xgboost_climate_model import ChannelFeatureEngineer, CONFIG  # noqa: E402

    feature_engineers = {
        "Solar": ChannelFeatureEngineer(
            "Solar",
            anchor_profile=norm_params["hourly_mean"][:, CHANNEL_IDX["Solar"]],
            latitude=CONFIG.latitude,
        ),
        "Temperature": ChannelFeatureEngineer(
            "Temperature",
            anchor_profile=norm_params["hourly_mean"][:, CHANNEL_IDX["Temperature"]],
            latitude=CONFIG.latitude,
        ),
    }

    split_diagnostics = {}
    for split_name in ["train", "val", "test"]:
        split_metrics, _, _ = evaluate_model(models, feature_engineers, splits[split_name])
        split_diagnostics[split_name] = {
            channel: {
                "MAE": float(values["MAE"]),
                "RMSE": float(values["RMSE"]),
                "BIAS": float(values["BIAS"]),
            }
            for channel, values in split_metrics.items()
        }

    new_metrics, new_point_pred, _ = evaluate_model(models, feature_engineers, test_data)

    point_rows = [
        metric_row(
            "Solar",
            test_truth_eval[:, CHANNEL_IDX["Solar"]],
            legacy_solar_test,
            new_point_pred[:, CHANNEL_IDX["Solar"]],
        ),
        metric_row(
            "Temperature",
            test_truth_eval[:, CHANNEL_IDX["Temperature"]],
            legacy_temp_test,
            new_point_pred[:, CHANNEL_IDX["Temperature"]],
            note="Legacy H2RES has no equivalent temperature generator; deterministic train-hour climatology is used as the non-ML baseline.",
        ),
        metric_row(
            "Wind diagnostic",
            test_truth_eval[:, CHANNEL_IDX["Wind"]],
            legacy_wind_test,
            new_point_pred[:, CHANNEL_IDX["Wind"]],
            note="Current pipeline uses wind block bootstrap for scenarios; the point metric uses persistence only as a diagnostic comparator.",
        ),
    ]
    point_df = pd.DataFrame(point_rows)

    print("[6/6] Generating current scenarios and evaluating distribution metrics...")
    scenarios = generate_scenarios(
        models,
        norm_params,
        num_scenarios=int(args.num_scenarios),
        history_data=splits["train"],
        seed=42,
    )

    truth_dist = test_data.reshape(-1, HOURS_PER_YEAR, 3).reshape(-1, 3)
    old_solar_dist = repeat_to_length(legacy.solar, int(args.num_scenarios) * HOURS_PER_YEAR)
    old_wind_dist = repeat_to_length(legacy.wind_onshore, int(args.num_scenarios) * HOURS_PER_YEAR)
    old_temp_dist = repeat_to_length(
        splits["hourly_mean"][:, CHANNEL_IDX["Temperature"]],
        int(args.num_scenarios) * HOURS_PER_YEAR,
    )
    new_dist = scenarios.reshape(-1, 3)

    dist_rows = [
        distribution_row(
            "Solar",
            truth_dist[:, CHANNEL_IDX["Solar"]],
            old_solar_dist,
            new_dist[:, CHANNEL_IDX["Solar"]],
        ),
        distribution_row(
            "Wind",
            truth_dist[:, CHANNEL_IDX["Wind"]],
            old_wind_dist,
            new_dist[:, CHANNEL_IDX["Wind"]],
        ),
        distribution_row(
            "Temperature",
            truth_dist[:, CHANNEL_IDX["Temperature"]],
            old_temp_dist,
            new_dist[:, CHANNEL_IDX["Temperature"]],
        ),
    ]
    distribution_df = pd.DataFrame(dist_rows)

    summary = {
        "method": "Leakage-safe train/validation/test split by complete historical years.",
        "data_file": str(PROJECT_DIR / "data" / "SC" / "HR_data.parquet"),
        "legacy_zip": str(legacy_zip),
        "legacy_ncre_member": legacy.source_ncre,
        "legacy_genco_member": legacy.source_genco,
        "raw_hours_after_leap_removal": int(len(raw_data)),
        "complete_years": int(len(raw_data) // HOURS_PER_YEAR),
        "train_years": int(len(splits["train"]) // HOURS_PER_YEAR),
        "validation_years": int(len(splits["val"]) // HOURS_PER_YEAR),
        "test_years": int(len(splits["test"]) // HOURS_PER_YEAR),
        "test_hours": int(len(splits["test"])),
        "test_hours_evaluated_after_warmup": int(len(test_truth_eval)),
        "num_generated_scenarios": int(args.num_scenarios),
        "legacy_solar_units": legacy.solar_units,
        "legacy_wind_onshore_units": legacy.wind_units,
        "legacy_wind_note": "Offshore wind was excluded from the wind validation because HR_data wind is an onshore/national wind series and the legacy offshore unit has a different resource basis.",
        "temperature_baseline_note": "Legacy H2RES does not provide a directly comparable temperature generator; train-hour climatology is used as the non-ML temperature baseline.",
        "current_pipeline_training_note": "XGBoost models were trained only on train years. Calibration used validation years. Test years were not used for fitting, calibration, or wind bootstrap sampling.",
        "current_pipeline_model_source": "Saved current pipeline models" if args.use_saved_current else "Freshly trained inside this validation run",
        "split_diagnostics": split_diagnostics,
        "point_metrics": point_df.to_dict(orient="records"),
        "distribution_metrics": distribution_df.to_dict(orient="records"),
    }

    point_path = out_dir / "H2RES_validation_point_metrics.csv"
    dist_path = out_dir / "H2RES_validation_distribution_metrics.csv"
    summary_path = out_dir / "H2RES_validation_summary.json"
    point_df.to_csv(point_path, index=False)
    distribution_df.to_csv(dist_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\nPoint metrics")
    print(point_df.to_string(index=False))
    print("\nDistribution metrics")
    print(distribution_df.to_string(index=False))
    print(f"\nSaved: {point_path}")
    print(f"Saved: {dist_path}")
    print(f"Saved: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

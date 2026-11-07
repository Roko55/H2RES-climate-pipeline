"""Compare legacy H2RES climate profiles with the current scenario ensemble.

This is the validation used for the short Sebastian-follow-up report.  It
compares what actually enters H2RES as climate input:

* legacy H2RES deterministic solar/wind profiles reconstructed from ncre/genco;
* current precomputed stochastic scenario ensemble from xgb_synthetic_scenarios.

The comparison is made against held-out historical test years.  The current
pipeline is evaluated through its ensemble mean profile and distributional
similarity, not as a deterministic hour-ahead weather forecast.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from xgboost_climate_model import CHANNEL_IDX, HOURS_PER_YEAR, load_climate_data, split_data
from validate_h2res_baseline_vs_climate_pipeline import (
    distribution_row,
    load_legacy_h2res_profiles,
    metric_row,
    repeat_to_length,
)


def _closest_year_sanity(scenarios: np.ndarray, splits: dict) -> pd.DataFrame:
    rows = []
    split_years = {
        name: values.reshape(-1, HOURS_PER_YEAR, 3)
        for name, values in splits.items()
        if name in {"train", "val", "test"}
    }
    for channel, idx in CHANNEL_IDX.items():
        for split_name, years in split_years.items():
            best_mae = float("inf")
            best_pair = (None, None)
            exact_copy = False
            for scenario_id in range(scenarios.shape[0]):
                exact_copy = exact_copy or bool(
                    np.any(
                        np.all(
                            np.isclose(
                                years[:, :, idx],
                                scenarios[scenario_id, :, idx][None, :],
                                atol=1e-10,
                            ),
                            axis=1,
                        )
                    )
                )
                diffs = np.mean(
                    np.abs(years[:, :, idx] - scenarios[scenario_id, :, idx][None, :]),
                    axis=1,
                )
                year_id = int(np.argmin(diffs))
                value = float(diffs[year_id])
                if value < best_mae:
                    best_mae = value
                    best_pair = (scenario_id, year_id)
            rows.append(
                {
                    "channel": channel,
                    "historical_split": split_name,
                    "min_whole_year_MAE_to_any_synthetic": best_mae,
                    "synthetic_id": int(best_pair[0]),
                    "historical_year_index_in_split": int(best_pair[1]),
                    "exact_year_copy_detected": exact_copy,
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--legacy-zip",
        default=str(Path.home() / "Downloads" / "wetransfer_h2res_new_share_2026-05-18_1425.zip"),
    )
    parser.add_argument(
        "--scenario-file",
        default=str(PROJECT_DIR / "data" / "SC" / "xgb_synthetic_scenarios.npy"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_DIR / "output" / "validation" / "sebastian_validation_profiles"),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_data = load_climate_data(str(PROJECT_DIR / "data" / "SC" / "HR_data.parquet"))
    splits = split_data(raw_data)
    test_data = splits["test"]
    truth_eval = test_data[168:]

    legacy = load_legacy_h2res_profiles(Path(args.legacy_zip))
    scenarios = np.load(args.scenario_file)
    scenario_mean = scenarios.mean(axis=0)

    old_solar = repeat_to_length(legacy.solar, len(test_data))[168:]
    old_wind = repeat_to_length(legacy.wind_onshore, len(test_data))[168:]
    old_temp = repeat_to_length(splits["hourly_mean"][:, CHANNEL_IDX["Temperature"]], len(test_data))[168:]

    new_solar = repeat_to_length(scenario_mean[:, CHANNEL_IDX["Solar"]], len(test_data))[168:]
    new_wind = repeat_to_length(scenario_mean[:, CHANNEL_IDX["Wind"]], len(test_data))[168:]
    new_temp = repeat_to_length(scenario_mean[:, CHANNEL_IDX["Temperature"]], len(test_data))[168:]

    profile_metrics = pd.DataFrame(
        [
            metric_row(
                "Solar profile",
                truth_eval[:, CHANNEL_IDX["Solar"]],
                old_solar,
                new_solar,
                "Current pipeline metric uses the synthetic ensemble mean hourly profile.",
            ),
            metric_row(
                "Wind profile",
                truth_eval[:, CHANNEL_IDX["Wind"]],
                old_wind,
                new_wind,
                "Current pipeline metric uses the synthetic ensemble mean hourly profile.",
            ),
            metric_row(
                "Temperature profile",
                truth_eval[:, CHANNEL_IDX["Temperature"]],
                old_temp,
                new_temp,
                "Legacy H2RES has no equivalent temperature generator; train-hour climatology is used as the non-ML baseline. Current pipeline metric uses the synthetic ensemble mean hourly profile.",
            ),
        ]
    )

    truth_dist = test_data.reshape(-1, HOURS_PER_YEAR, 3).reshape(-1, 3)
    n_scenarios = scenarios.shape[0]
    distribution_metrics = pd.DataFrame(
        [
            distribution_row(
                "Solar",
                truth_dist[:, CHANNEL_IDX["Solar"]],
                repeat_to_length(legacy.solar, n_scenarios * HOURS_PER_YEAR),
                scenarios.reshape(-1, 3)[:, CHANNEL_IDX["Solar"]],
            ),
            distribution_row(
                "Wind",
                truth_dist[:, CHANNEL_IDX["Wind"]],
                repeat_to_length(legacy.wind_onshore, n_scenarios * HOURS_PER_YEAR),
                scenarios.reshape(-1, 3)[:, CHANNEL_IDX["Wind"]],
            ),
            distribution_row(
                "Temperature",
                truth_dist[:, CHANNEL_IDX["Temperature"]],
                repeat_to_length(
                    splits["hourly_mean"][:, CHANNEL_IDX["Temperature"]],
                    n_scenarios * HOURS_PER_YEAR,
                ),
                scenarios.reshape(-1, 3)[:, CHANNEL_IDX["Temperature"]],
            ),
        ]
    )

    leakage_sanity = _closest_year_sanity(scenarios, splits)

    summary = {
        "method": "Legacy deterministic H2RES profile vs current precomputed stochastic scenario ensemble on held-out historical test years.",
        "complete_years": int(len(raw_data) // HOURS_PER_YEAR),
        "train_years": int(len(splits["train"]) // HOURS_PER_YEAR),
        "validation_years": int(len(splits["val"]) // HOURS_PER_YEAR),
        "test_years": int(len(splits["test"]) // HOURS_PER_YEAR),
        "test_hours_evaluated_after_warmup": int(len(truth_eval)),
        "legacy_solar_units": legacy.solar_units,
        "legacy_wind_onshore_units": legacy.wind_units,
        "scenario_shape": list(scenarios.shape),
        "notes": [
            "Offshore wind is excluded from the legacy wind baseline because the HR historical wind series is an onshore/national series.",
            "Temperature uses train-hour climatology as the non-ML baseline because legacy H2RES has no directly comparable temperature generator.",
            "Metrics evaluate input profile and distribution quality, not Gurobi system outputs.",
        ],
    }

    profile_metrics.to_csv(output_dir / "H2RES_validation_profile_metrics.csv", index=False)
    distribution_metrics.to_csv(output_dir / "H2RES_validation_distribution_metrics.csv", index=False)
    leakage_sanity.to_csv(output_dir / "H2RES_validation_leakage_sanity.csv", index=False)
    (output_dir / "H2RES_validation_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print("\nProfile metrics")
    print(profile_metrics.to_string(index=False))
    print("\nDistribution metrics")
    print(distribution_metrics.to_string(index=False))
    print(f"\nSaved validation outputs to: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run a compact component ablation for the H2RES climate input pipeline.

The analysis separates the contribution of the current climate workflow into
three interpretable steps:

1. legacy H2RES renewable profiles with a climatological temperature profile;
2. current wind profile only;
3. current wind and solar profiles;
4. full current profile, including temperature.

The script is intentionally input-focused.  It avoids mixing the old solver
asset layout with the current NUTS3/macro asset layout, while still showing
which climate component changes the data that is fed into H2RES.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data" / "SC"
DEFAULT_LEGACY_ZIP = (
    Path.home() / "Downloads" / "wetransfer_h2res_new_share_2026-05-18_1425.zip"
)

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import scenario_manager  # noqa: E402
from validate_h2res_baseline_vs_climate_pipeline import (  # noqa: E402
    load_legacy_h2res_profiles,
)
from xgboost_climate_model import (  # noqa: E402
    CHANNEL_IDX,
    HOURS_PER_YEAR,
    load_climate_data,
    split_data,
)


@dataclass(frozen=True)
class VariantProfile:
    name: str
    description: str
    solar: np.ndarray
    wind: np.ndarray
    temperature: np.ndarray


def _as_8760(values: np.ndarray, label: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float).reshape(-1)
    if len(arr) != HOURS_PER_YEAR:
        raise ValueError(f"{label} must contain {HOURS_PER_YEAR} values, found {len(arr)}.")
    return arr


def _load_baseline_temperature() -> np.ndarray:
    raw_data = load_climate_data()
    splits = split_data(raw_data)
    return _as_8760(splits["hourly_mean"][:, CHANNEL_IDX["Temperature"]], "baseline temperature")


def _active_capacity_mw(genco_path: Path, technology: str, fuel_keywords: Iterable[str]) -> float:
    if not genco_path.exists():
        raise FileNotFoundError(f"Missing genco file: {genco_path}")

    genco = pd.read_csv(genco_path)
    tech = genco.get("technology", pd.Series("", index=genco.index)).astype(str).str.upper()
    fuel = genco.get("fuel_type", pd.Series("", index=genco.index)).astype(str).str.upper()
    cap = pd.to_numeric(genco.get("cap_mw", 0.0), errors="coerce").fillna(0.0)

    mask = tech.eq(str(technology).upper())
    for keyword in fuel_keywords:
        mask = mask | fuel.str.contains(str(keyword).upper(), na=False)

    return float(cap[mask & (cap > 0.0)].sum())


def _cop_proxy(temp_c: np.ndarray, sink_temp_c: float = 45.0, carnot_efficiency: float = 0.36) -> np.ndarray:
    source_k = np.asarray(temp_c, dtype=float) + 273.15
    sink_k = float(sink_temp_c) + 273.15
    delta_k = np.maximum(sink_k - source_k, 5.0)
    cop = float(carnot_efficiency) * sink_k / delta_k
    return np.clip(cop, 1.2, 5.5)


def _metrics_for_variant(profile: VariantProfile, solar_cap_mw: float, wind_cap_mw: float) -> Dict[str, float | str]:
    solar = np.clip(profile.solar, 0.0, 1.0)
    wind = np.clip(profile.wind, 0.0, 1.0)
    temp = np.asarray(profile.temperature, dtype=float)
    hdd18 = np.maximum(18.0 - temp, 0.0)
    cdd22 = np.maximum(temp - 22.0, 0.0)
    cop = _cop_proxy(temp)

    solar_gwh = float(solar.sum() * solar_cap_mw / 1000.0)
    wind_gwh = float(wind.sum() * wind_cap_mw / 1000.0)

    return {
        "variant": profile.name,
        "description": profile.description,
        "solar_cf_mean": float(solar.mean()),
        "wind_cf_mean": float(wind.mean()),
        "temperature_mean_c": float(temp.mean()),
        "temperature_min_c": float(temp.min()),
        "temperature_max_c": float(temp.max()),
        "solar_proxy_gwh_current_capacity": solar_gwh,
        "wind_proxy_gwh_current_capacity": wind_gwh,
        "total_res_proxy_gwh_current_capacity": solar_gwh + wind_gwh,
        "hdd18_sum": float(hdd18.sum()),
        "cdd22_sum": float(cdd22.sum()),
        "cop_proxy_mean": float(cop.mean()),
        "cop_proxy_min": float(cop.min()),
        "cop_proxy_max": float(cop.max()),
    }


def _add_deltas(summary: pd.DataFrame, metric_columns: list[str]) -> pd.DataFrame:
    baseline = summary.iloc[0]
    previous = summary.shift(1)
    out = summary[["variant", "description"]].copy()

    for column in metric_columns:
        base_value = float(baseline[column])
        prev_values = previous[column].astype(float)
        values = summary[column].astype(float)
        out[f"{column}_delta_vs_legacy"] = values - base_value
        out[f"{column}_pct_vs_legacy"] = np.where(
            abs(base_value) > 1e-12,
            (values - base_value) / abs(base_value) * 100.0,
            np.nan,
        )
        out[f"{column}_incremental_delta"] = values - prev_values

    out.loc[0, [c for c in out.columns if c.endswith("_incremental_delta")]] = 0.0
    return out


def _write_profiles_csv(variants: list[VariantProfile], out_path: Path) -> None:
    data: Dict[str, np.ndarray] = {"hour": np.arange(1, HOURS_PER_YEAR + 1, dtype=int)}
    for variant in variants:
        data[f"{variant.name}_solar_cf"] = variant.solar
        data[f"{variant.name}_wind_cf"] = variant.wind
        data[f"{variant.name}_temperature_c"] = variant.temperature
    pd.DataFrame(data).to_csv(out_path, index=False)


def _markdown_table(df: pd.DataFrame, float_digits: int = 4) -> str:
    display = df.copy()
    for column in display.columns:
        if pd.api.types.is_float_dtype(display[column]):
            display[column] = display[column].map(lambda value: f"{value:.{float_digits}f}")
        else:
            display[column] = display[column].astype(str)

    headers = list(display.columns)
    rows = display.values.tolist()
    widths = [
        max(len(str(header)), *(len(str(row[i])) for row in rows)) if rows else len(str(header))
        for i, header in enumerate(headers)
    ]

    def fmt_row(values: Iterable[str]) -> str:
        return "| " + " | ".join(str(value).ljust(widths[i]) for i, value in enumerate(values)) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join([fmt_row(headers), separator, *(fmt_row(row) for row in rows)])


def _write_report(
    out_path: Path,
    scenario_name: str,
    year: int,
    probability: float,
    summary: pd.DataFrame,
    deltas: pd.DataFrame,
    diagnostics: Dict,
    solar_cap_mw: float,
    wind_cap_mw: float,
) -> None:
    key_columns = [
        "variant",
        "solar_cf_mean",
        "wind_cf_mean",
        "temperature_mean_c",
        "solar_proxy_gwh_current_capacity",
        "wind_proxy_gwh_current_capacity",
        "hdd18_sum",
        "cdd22_sum",
        "cop_proxy_mean",
    ]
    delta_columns = [
        "variant",
        "total_res_proxy_gwh_current_capacity_delta_vs_legacy",
        "total_res_proxy_gwh_current_capacity_pct_vs_legacy",
        "hdd18_sum_pct_vs_legacy",
        "cdd22_sum_pct_vs_legacy",
        "cop_proxy_mean_pct_vs_legacy",
    ]

    text = [
        "# H2RES climate pipeline component ablation",
        "",
        f"Scenario: `{scenario_name}`",
        f"Planning year: `{year}`",
        f"Scenario probability in the current ensemble: `{probability:.4f}`",
        "",
        "This is an input-layer ablation. It isolates climate-profile changes before",
        "capacity-expansion decisions, so the proxy generation values use the current",
        "HR asset capacities for all variants.",
        "",
        f"Current solar capacity used for proxies: `{solar_cap_mw:.3f} MW`",
        f"Current onshore wind capacity used for proxies: `{wind_cap_mw:.3f} MW`",
        "",
        "## Main metrics",
        "",
        _markdown_table(summary[key_columns], float_digits=4),
        "",
        "## Changes versus legacy baseline",
        "",
        _markdown_table(deltas[delta_columns], float_digits=4),
        "",
        "## Current decomposition diagnostics",
        "",
        f"Solar source resolution: `{diagnostics.get('solar_resolution_selected', 'n/a')}`",
        f"Solar source zone count: `{diagnostics.get('solar_source_zone_count', 'n/a')}`",
        f"Solar mass mean absolute error: `{diagnostics.get('solar_mass_mean_abs_error', np.nan):.8f}`",
        f"Wind source resolution: `{diagnostics.get('wind_resolution_selected', 'n/a')}`",
        f"Wind site count: `{diagnostics.get('wind_site_count', 'n/a')}`",
        f"Active wind NUTS3 count: `{diagnostics.get('active_wind_nuts3_count', 'n/a')}`",
        f"Wind mass mean absolute error: `{diagnostics.get('wind_mass_mean_abs_error', np.nan):.8f}`",
        "",
        "## Interpretation",
        "",
        "- `wind_only` shows the isolated effect of replacing the legacy wind profile.",
        "- `res_layer_no_temp` adds the current solar profile while keeping temperature neutral.",
        "- `full_pipeline` adds the generated temperature profile and therefore changes HDD, CDD and COP proxy indicators.",
        "- The next solver-level step should run only the variants whose input differences are large enough to justify full Gurobi runtime.",
        "",
    ]
    out_path.write_text("\n".join(text), encoding="utf-8")


def run(args: argparse.Namespace) -> Path:
    legacy_zip = Path(args.legacy_zip)
    if not legacy_zip.exists():
        raise FileNotFoundError(f"Legacy ZIP not found: {legacy_zip}")

    scenario_name = str(args.scenario)
    year = int(args.year)

    legacy = load_legacy_h2res_profiles(legacy_zip)
    baseline_temp = _load_baseline_temperature()

    scenarios = scenario_manager.get_representative_scenarios(year=year)
    if scenario_name not in scenarios:
        available = ", ".join(sorted(k for k in scenarios.keys() if k != "metadata"))
        raise KeyError(f"Scenario '{scenario_name}' not found. Available: {available}")

    current_profile = np.asarray(scenarios[scenario_name]["profile"], dtype=float)
    if current_profile.shape != (HOURS_PER_YEAR, 3):
        raise ValueError(f"Scenario profile must be {(HOURS_PER_YEAR, 3)}, found {current_profile.shape}.")

    current_solar = _as_8760(current_profile[:, CHANNEL_IDX["Solar"]], "current solar")
    current_wind = _as_8760(current_profile[:, CHANNEL_IDX["Wind"]], "current wind")
    current_temp = _as_8760(current_profile[:, CHANNEL_IDX["Temperature"]], "current temperature")

    variants = [
        VariantProfile(
            name="legacy_baseline",
            description="Legacy H2RES solar/wind profiles plus climatological temperature.",
            solar=_as_8760(legacy.solar, "legacy solar"),
            wind=_as_8760(legacy.wind_onshore, "legacy wind"),
            temperature=baseline_temp,
        ),
        VariantProfile(
            name="wind_only",
            description="Current wind profile, legacy solar, climatological temperature.",
            solar=_as_8760(legacy.solar, "legacy solar"),
            wind=current_wind,
            temperature=baseline_temp,
        ),
        VariantProfile(
            name="res_layer_no_temp",
            description="Current wind and solar profiles, climatological temperature.",
            solar=current_solar,
            wind=current_wind,
            temperature=baseline_temp,
        ),
        VariantProfile(
            name="full_pipeline",
            description="Current wind, solar and temperature profiles.",
            solar=current_solar,
            wind=current_wind,
            temperature=current_temp,
        ),
    ]

    genco_path = DATA_DIR / "genco_data_HR_sdewes.csv"
    solar_cap_mw = _active_capacity_mw(genco_path, technology="PHOT", fuel_keywords=["solar"])
    wind_cap_mw = _active_capacity_mw(genco_path, technology="WTON", fuel_keywords=["wind"])

    summary = pd.DataFrame(
        [_metrics_for_variant(profile, solar_cap_mw, wind_cap_mw) for profile in variants]
    )

    metric_columns = [
        "solar_cf_mean",
        "wind_cf_mean",
        "temperature_mean_c",
        "solar_proxy_gwh_current_capacity",
        "wind_proxy_gwh_current_capacity",
        "total_res_proxy_gwh_current_capacity",
        "hdd18_sum",
        "cdd22_sum",
        "cop_proxy_mean",
    ]
    deltas = _add_deltas(summary, metric_columns)

    _, diagnostics = scenario_manager.decompose_to_h2res_units(
        current_profile,
        country_code=args.country,
        wind_resolution_mode=args.wind_resolution_mode,
        return_diagnostics=True,
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir) / f"{timestamp}_{args.country}_{scenario_name}_{year}"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "component_ablation_summary.csv"
    deltas_path = out_dir / "component_ablation_deltas_vs_legacy.csv"
    profiles_path = out_dir / "component_ablation_profiles.csv"
    diagnostics_path = out_dir / "component_ablation_diagnostics.json"
    report_path = out_dir / "component_ablation_report.md"

    summary.to_csv(summary_path, index=False)
    deltas.to_csv(deltas_path, index=False)
    _write_profiles_csv(variants, profiles_path)
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    _write_report(
        report_path,
        scenario_name=scenario_name,
        year=year,
        probability=float(scenarios[scenario_name]["probability"]),
        summary=summary,
        deltas=deltas,
        diagnostics=diagnostics,
        solar_cap_mw=solar_cap_mw,
        wind_cap_mw=wind_cap_mw,
    )

    print("\nComponent ablation complete.")
    print(f"Output directory: {out_dir}")
    print("\nMain metrics:")
    print(summary[[
        "variant",
        "solar_cf_mean",
        "wind_cf_mean",
        "temperature_mean_c",
        "solar_proxy_gwh_current_capacity",
        "wind_proxy_gwh_current_capacity",
        "hdd18_sum",
        "cdd22_sum",
        "cop_proxy_mean",
    ]].to_string(index=False))
    print("\nChanges versus legacy baseline:")
    print(deltas[[
        "variant",
        "total_res_proxy_gwh_current_capacity_delta_vs_legacy",
        "total_res_proxy_gwh_current_capacity_pct_vs_legacy",
        "hdd18_sum_pct_vs_legacy",
        "cdd22_sum_pct_vs_legacy",
        "cop_proxy_mean_pct_vs_legacy",
    ]].to_string(index=False))

    return out_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run component ablation for the H2RES climate input pipeline."
    )
    parser.add_argument("--country", default="HR")
    parser.add_argument("--year", type=int, default=2020)
    parser.add_argument("--scenario", default="avg_yield__normal")
    parser.add_argument("--wind-resolution-mode", default="auto", choices=["auto", "nuts3", "macro"])
    parser.add_argument("--legacy-zip", default=str(DEFAULT_LEGACY_ZIP))
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_DIR / "output" / "component_ablation"),
    )
    return parser.parse_args()


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

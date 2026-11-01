"""Generate climate-scenario input packages for the H2RES_new_share model.

The newer H2RES share has a different model structure than the older
single-zone integration. This adapter keeps the new model unchanged and writes
scenario-specific copies of the input files that are already read by main.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


SCENARIOS = ("low_yield__normal", "avg_yield__normal", "high_yield__normal")
THERMAL_CASE_QUANTILES = {"cold": 0.10, "normal": 0.50, "hot": 0.90}
WIND_TOKENS = ("wind", "offshore")
SOLAR_TOKENS = ("solar", "pv")


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class ScenarioPackage:
    scenario: str
    root: Path
    ncre_path: Path
    genco_path: Path
    inflows_path: Path
    policy_path: Path
    heat_indv_path: Path | None
    heat_dh_path: Path | None
    cool_indv_path: Path | None
    cool_dh_path: Path | None
    summary_path: Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_representative_profiles(year: int) -> dict[str, dict]:
    from scripts.climate_runtime_inputs import get_representative_scenarios

    return get_representative_scenarios(planning_year=year)


def _training_temperature_stress_offsets(data_path: Path) -> tuple[dict[str, float], dict[str, object]]:
    """Return P10/P50/P90 annual-temperature offsets from training years only."""
    frame = pd.read_parquet(data_path, columns=["time", "temperature"])
    frame["time"] = pd.to_datetime(frame["time"], errors="raise")
    annual = (
        frame.assign(year=frame["time"].dt.year)
        .groupby("year", sort=True)["temperature"]
        .mean()
    )
    # Match the manuscript split: the first 36 complete years form the training pool.
    training = annual.iloc[:36]
    quantiles = {
        name: float(training.quantile(quantile))
        for name, quantile in THERMAL_CASE_QUANTILES.items()
    }
    median = quantiles["normal"]
    offsets = {name: value - median for name, value in quantiles.items()}
    diagnostics = {
        "calibration_source": str(data_path),
        "training_year_start": int(training.index.min()),
        "training_year_end": int(training.index.max()),
        "training_year_count": int(len(training)),
        "annual_mean_temperature_quantiles_c": quantiles,
        "temperature_offsets_c": offsets,
    }
    return offsets, diagnostics


def _align_profile(values: np.ndarray, n: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) == n:
        return values
    if len(values) > n:
        return values[:n]
    reps = int(np.ceil(n / max(1, len(values))))
    return np.tile(values, reps)[:n]


def _preserve_total_under_cap(values: np.ndarray, target_total: float, cap: float) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if target_total <= 0 or cap <= 0:
        return np.zeros_like(values)

    capped = np.minimum(np.maximum(values, 0.0), cap)
    feasible_total = min(float(target_total), cap * len(capped))

    for _ in range(20):
        diff = feasible_total - float(capped.sum())
        if abs(diff) <= max(1e-8, feasible_total * 1e-10):
            break
        if diff > 0:
            room = np.maximum(cap - capped, 0.0)
            room_sum = float(room.sum())
            if room_sum <= 1e-12:
                break
            capped = np.minimum(capped + room * min(1.0, diff / room_sum), cap)
        else:
            positive = capped > 0
            positive_sum = float(capped[positive].sum())
            if positive_sum <= 1e-12:
                break
            capped[positive] = np.maximum(capped[positive] + capped[positive] * (diff / positive_sum), 0.0)

    return capped


def _renewable_units(genco: pd.DataFrame, token_group: tuple[str, ...]) -> dict[str, float]:
    df = genco.copy()
    fuel = df.get("fuel_type", pd.Series("", index=df.index)).astype(str).str.lower()
    tech = df.get("technology", pd.Series("", index=df.index)).astype(str).str.lower()
    system = df.get("System", pd.Series("", index=df.index)).astype(str).str.upper()
    names = df.get("unit_name", pd.Series("", index=df.index)).astype(str)
    mask = system.eq("HR")
    token_mask = np.zeros(len(df), dtype=bool)
    for token in token_group:
        token = token.lower()
        token_mask |= fuel.str.contains(token, na=False).to_numpy()
        token_mask |= tech.str.contains(token, na=False).to_numpy()
        token_mask |= names.str.lower().str.contains(token, na=False).to_numpy()
    out = df.loc[mask & token_mask, ["unit_name", "cap_mw"]].copy()
    out["cap_mw"] = pd.to_numeric(out["cap_mw"], errors="coerce").fillna(0.0)
    return out.groupby("unit_name")["cap_mw"].sum().to_dict()


def _capacity_weighted_average(df: pd.DataFrame, cols: list[str], cap_map: dict[str, float]) -> np.ndarray:
    weights = np.array([max(float(cap_map.get(c, 0.0)), 0.0) for c in cols], dtype=float)
    if weights.sum() <= 0:
        weights = np.ones(len(cols), dtype=float)
    weights = weights / weights.sum()
    return df[cols].to_numpy(dtype=float).dot(weights)


def _apply_profile_to_columns(
    df: pd.DataFrame,
    profile: np.ndarray,
    cols: list[str],
    cap_map: dict[str, float],
) -> tuple[pd.DataFrame, float]:
    if not cols:
        return df, float("nan")
    n = len(df)
    target = _align_profile(profile, n)
    base_avg = _capacity_weighted_average(df, cols, cap_map)
    eps = 1e-6
    for col in cols:
        ratio = np.divide(
            df[col].to_numpy(dtype=float),
            np.where(base_avg > eps, base_avg, 1.0),
            out=np.ones(n, dtype=float),
            where=base_avg > eps,
        )
        df[col] = np.clip(target * ratio, 0.0, 1.0)
    after = _capacity_weighted_average(df, cols, cap_map)
    return df, float(np.mean(after))


def _hourly_mean_8760(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        if "time" not in work.columns:
            raise ValueError("Spatial climate profiles require a DatetimeIndex or a time column")
        work["time"] = pd.to_datetime(work["time"], errors="coerce")
        work = work.dropna(subset=["time"]).set_index("time")
    work = work.sort_index()
    if work.index.tz is not None:
        work.index = work.index.tz_localize(None)
    work = work.loc[~((work.index.month == 2) & (work.index.day == 29))]
    work = work.select_dtypes(include=[np.number])

    idx = work.index
    leap = idx.is_leap_year.astype(int)
    after_february = ((idx.month > 2) | ((idx.month == 2) & (idx.day > 28))).astype(int)
    hour = (idx.dayofyear - leap * after_february - 1) * 24 + idx.hour
    hourly = work.assign(hour_of_year=hour).groupby("hour_of_year").mean(numeric_only=True)
    return hourly.reindex(np.arange(8760)).interpolate(limit_direction="both").ffill().bfill()


def _technology_mask(genco: pd.DataFrame, technology: str, country_code: str = "HR") -> pd.Series:
    return (
        genco["System"].astype(str).str.upper().eq(country_code.upper())
        & genco["technology"].astype(str).str.upper().eq(technology.upper())
    )


def _build_climate_genco(base_path: Path, target_path: Path, climate_data_dir: Path) -> tuple[pd.DataFrame, dict]:
    genco = pd.read_csv(base_path)
    required = {"unit_name", "cap_mw", "max_cap", "fuel_type", "technology", "System", "Zone"}
    missing = sorted(required - set(genco.columns))
    if missing:
        raise ValueError(f"NewShare genco schema is missing columns: {missing}")

    asset_specs = {
        "WTON": {
            "metadata": climate_data_dir / "HR_wind_nuts_metadata.csv",
            "unit_col": "units",
            "cap_col": "total_cap_mw",
            "template_name": "HR_Wind",
        },
        "PHOT": {
            "metadata": climate_data_dir / "HR_solar_unit_metadata.csv",
            "unit_col": "unit_name",
            "cap_col": "cap_mw",
            "template_name": "HR_PV",
        },
    }

    diagnostics: dict[str, float | int | list[str]] = {}
    replacement_rows: list[pd.Series] = []
    remove_mask = pd.Series(False, index=genco.index)

    for technology, spec in asset_specs.items():
        tech_mask = _technology_mask(genco, technology)
        existing = genco.loc[tech_mask].copy()
        if existing.empty:
            raise ValueError(f"No HR {technology} rows found in {base_path}")
        remove_mask |= tech_mask

        metadata = pd.read_csv(spec["metadata"])
        metadata[spec["unit_col"]] = metadata[spec["unit_col"]].astype(str).str.strip()
        metadata[spec["cap_col"]] = pd.to_numeric(metadata[spec["cap_col"]], errors="coerce").fillna(0.0)
        metadata = metadata.loc[metadata[spec["cap_col"]] > 0].copy()
        if metadata.empty:
            raise ValueError(f"No positive-capacity assets found in {spec['metadata']}")

        template_match = existing.loc[existing["unit_name"].astype(str).eq(spec["template_name"])]
        template = (template_match.iloc[0] if not template_match.empty else existing.iloc[0]).copy()
        old_cap = float(pd.to_numeric(existing["cap_mw"], errors="coerce").fillna(0.0).sum())
        old_max = float(pd.to_numeric(existing["max_cap"], errors="coerce").fillna(0.0).sum())
        asset_total = float(metadata[spec["cap_col"]].sum())

        for record in metadata.itertuples(index=False):
            row = template.copy()
            unit = str(getattr(record, spec["unit_col"]))
            cap = float(getattr(record, spec["cap_col"]))
            row["unit_name"] = unit
            row["cap_mw"] = cap
            row["max_cap"] = max(cap, old_max * cap / asset_total)
            row["System"] = "HR"
            row["Zone"] = "HR1"
            replacement_rows.append(row)

        label = "wind" if technology == "WTON" else "solar"
        diagnostics[f"{label}_legacy_cap_mw"] = old_cap
        diagnostics[f"{label}_asset_cap_mw"] = asset_total
        diagnostics[f"{label}_max_cap_mw_preserved"] = old_max
        diagnostics[f"{label}_unit_count"] = int(len(metadata))
        diagnostics[f"{label}_units"] = metadata[spec["unit_col"]].tolist()

    first_removed = int(np.flatnonzero(remove_mask.to_numpy())[0])
    replacement = pd.DataFrame(replacement_rows, columns=genco.columns)
    rebuilt = pd.concat(
        [genco.iloc[:first_removed].loc[~remove_mask.iloc[:first_removed]], replacement,
         genco.iloc[first_removed:].loc[~remove_mask.iloc[first_removed:]]],
        ignore_index=True,
    )
    climate_units = [str(row["unit_name"]) for row in replacement_rows]
    kept_units = set(genco.loc[~remove_mask, "unit_name"].astype(str))
    collisions = sorted(set(climate_units) & kept_units)
    if len(climate_units) != len(set(climate_units)) or collisions:
        raise ValueError(f"Climate asset units are not unique in the rebuilt genco data: {collisions}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    rebuilt.to_csv(target_path, index=False)
    return rebuilt, diagnostics


def _balance_weighted_profiles(values: np.ndarray, weights: np.ndarray, target: np.ndarray) -> np.ndarray:
    balanced = np.clip(np.asarray(values, dtype=float), 0.0, 1.0)
    weights = np.asarray(weights, dtype=float)
    weights = weights / weights.sum()
    target = np.clip(np.asarray(target, dtype=float), 0.0, 1.0)

    for row_idx in range(balanced.shape[0]):
        row = balanced[row_idx]
        current = float(row @ weights)
        desired = float(target[row_idx])
        if current > desired and current > 0.0:
            row *= desired / current
        elif current < desired:
            room = 1.0 - row
            weighted_room = float(room @ weights)
            if weighted_room > 0.0:
                row += room * min(1.0, (desired - current) / weighted_room)
        balanced[row_idx] = np.clip(row, 0.0, 1.0)
    return balanced


def _spatial_unit_profiles(
    target_profile: np.ndarray,
    source_path: Path,
    transfer_path: Path,
    unit_capacities: dict[str, float],
) -> tuple[pd.DataFrame, dict]:
    transfer = pd.read_csv(transfer_path)
    required = {"unit_name", "source_zone", "weight"}
    if not required.issubset(transfer.columns):
        raise ValueError(f"Transfer matrix {transfer_path} is missing {sorted(required - set(transfer.columns))}")
    transfer["unit_name"] = transfer["unit_name"].astype(str).str.strip()
    transfer["source_zone"] = transfer["source_zone"].astype(str).str.strip()
    transfer["weight"] = pd.to_numeric(transfer["weight"], errors="coerce").fillna(0.0).clip(lower=0.0)

    units = list(unit_capacities)
    pivot = transfer.pivot_table(
        index="unit_name", columns="source_zone", values="weight", aggfunc="sum", fill_value=0.0
    ).reindex(units, fill_value=0.0)
    if (pivot.sum(axis=1) <= 0).any():
        missing_units = pivot.index[pivot.sum(axis=1) <= 0].tolist()
        raise ValueError(f"Units without a spatial transfer mapping in {transfer_path}: {missing_units}")
    pivot = pivot.div(pivot.sum(axis=1), axis=0)

    spatial_history = pd.read_parquet(source_path)
    source_dates = pd.to_datetime(
        spatial_history["time"] if "time" in spatial_history.columns else spatial_history.index
    )
    training_mask = (source_dates.year <= 2015) if isinstance(source_dates, pd.DatetimeIndex) else source_dates.dt.year.le(2015)
    spatial_history = spatial_history.loc[training_mask].copy()
    if spatial_history.empty:
        raise ValueError("No training-period spatial observations available")
    source = _hourly_mean_8760(spatial_history)
    zones = [zone for zone in pivot.columns if zone in source.columns]
    if not zones:
        raise ValueError(f"No common source zones between {source_path} and {transfer_path}")
    pivot = pivot[zones]
    pivot = pivot.div(pivot.sum(axis=1), axis=0)

    caps = pd.Series(unit_capacities, dtype=float).reindex(units)
    unit_cap_weights = caps / float(caps.sum())
    zone_cap = pivot.mul(caps, axis=0).sum(axis=0)
    zone_weights = zone_cap / float(zone_cap.sum())
    historical = source[zones].astype(float).clip(0.0, 1.0)
    historical_aggregate = historical.mul(zone_weights, axis=1).sum(axis=1).to_numpy(dtype=float)
    ratios = np.divide(
        historical.to_numpy(dtype=float),
        historical_aggregate.reshape(-1, 1),
        out=np.ones_like(historical.to_numpy(dtype=float)),
        where=historical_aggregate.reshape(-1, 1) > 1e-8,
    )

    target = _align_profile(target_profile, 8760)
    zone_profiles = _balance_weighted_profiles(ratios * target.reshape(-1, 1), zone_weights.to_numpy(), target)
    unit_profiles = zone_profiles @ pivot.to_numpy(dtype=float).T
    aggregate = unit_profiles @ unit_cap_weights.to_numpy(dtype=float)
    mass_error = float(np.mean(np.abs(aggregate - target)))

    return pd.DataFrame(unit_profiles, columns=units), {
        "spatial_reference_end_year": 2015,
        "spatial_reference_rows": len(spatial_history),
        "source_zone_count": int(len(zones)),
        "source_zones": zones,
        "unit_count": int(len(units)),
        "units": units,
        "mass_mean_abs_error": mass_error,
        "aggregate_mean_cf": float(np.mean(aggregate)),
    }


def _drop_legacy_columns(df: pd.DataFrame, legacy_units: set[str]) -> pd.DataFrame:
    def base_name(column: str) -> str:
        return re.sub(r"\.\d+$", "", str(column))

    drop = [column for column in df.columns if base_name(column) in legacy_units]
    return df.drop(columns=drop, errors="ignore")


def _write_year_specific_curves(base_path: Path, target_path: Path, years: list[int]) -> None:
    base = pd.read_csv(base_path)
    if "period" not in base.columns:
        raise ValueError(f"Year-specific curve source has no period column: {base_path}")
    frames = []
    for planning_year in years:
        frame = base.copy()
        frame.insert(0, "year", int(planning_year))
        frames.append(frame)
    pd.concat(frames, ignore_index=True).to_csv(target_path, index=False)


def _write_quick_policy(
    base_policy: Path,
    target_policy: Path,
    years: int,
    hours: int,
    start_year: int = 2020,
) -> None:
    if years < 2:
        years = 2
    shutil.copy2(base_policy, target_policy)
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to write quick policy files") from exc

    wb = load_workbook(target_policy, data_only=True)
    ws = wb["setup"]
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)
    for offset in range(years):
        row = 2 + offset
        ws.cell(row=row, column=1, value=start_year + offset)
        ws.cell(row=row, column=2, value=1 if offset == 0 else None)
        ws.cell(row=row, column=3, value=1 if offset == 0 else None)
        ws.cell(row=row, column=4, value=1 if offset == 0 else None)
        ws.cell(row=row, column=5, value=hours if offset == 0 else None)
    wb.save(target_policy)


def _write_selected_year_policy(
    base_policy: Path,
    target_policy: Path,
    selected_years: list[int],
    hours: int,
) -> None:
    if not selected_years:
        raise ValueError("selected_years must contain at least one year")
    shutil.copy2(base_policy, target_policy)
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to write policy files") from exc

    wb = load_workbook(target_policy, data_only=True)
    ws = wb["setup"]
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)
    for offset, planning_year in enumerate(selected_years):
        row = 2 + offset
        ws.cell(row=row, column=1, value=int(planning_year))
        ws.cell(row=row, column=2, value=1 if offset == 0 else None)
        ws.cell(row=row, column=3, value=1 if offset == 0 else None)
        ws.cell(row=row, column=4, value=1 if offset == 0 else None)
        ws.cell(row=row, column=5, value=hours if offset == 0 else None)
    wb.save(target_policy)


def _scale_demand_file(
    base_path: Path,
    target_path: Path,
    scenario_temp: np.ndarray,
    normal_temp: np.ndarray,
    mode: str,
    peak_safe: bool = False,
    peak_limit_ratio: float = 1.0,
    strength_scale: float = 1.0,
) -> dict[str, float]:
    df = pd.read_csv(base_path)
    n = len(df)
    temp = _align_profile(scenario_temp, n)
    normal = _align_profile(normal_temp, n)
    if mode == "heat":
        scenario_driver = np.maximum(18.0 - temp, 0.0)
        normal_driver = np.maximum(18.0 - normal, 0.0)
        strength = 0.35
    elif mode == "cool":
        scenario_driver = np.maximum(temp - 22.0, 0.0)
        normal_driver = np.maximum(normal - 22.0, 0.0)
        strength = 0.25
    else:
        raise ValueError(mode)
    strength *= float(strength_scale)

    positive_normal = normal_driver[normal_driver > 0]
    if len(positive_normal):
        reference_driver = max(float(np.percentile(positive_normal, 75)), 1.0)
    else:
        reference_driver = 1.0

    driver_delta = scenario_driver - normal_driver
    factor = 1.0 + strength * (driver_delta / reference_driver)
    factor = np.clip(factor, 0.75, 1.25)
    factor = (
        pd.Series(factor)
        .rolling(window=24, center=True, min_periods=1)
        .mean()
        .to_numpy(dtype=float)
    )
    changed = 0
    peak_ratios: list[float] = []
    for col in [c for c in df.columns if c != "period"]:
        base = pd.to_numeric(df[col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        total = float(base.sum())
        if total <= 0:
            continue
        scaled = base * factor
        scaled_sum = float(scaled.sum())
        if scaled_sum > 0:
            scaled = scaled * (total / scaled_sum)
        base_peak = float(np.max(base))
        if peak_safe and base_peak > 0:
            scaled = _preserve_total_under_cap(scaled, total, base_peak * float(peak_limit_ratio))
        scaled_peak = float(np.max(scaled))
        if base_peak > 0:
            peak_ratios.append(scaled_peak / base_peak)
        df[col] = scaled
        changed += 1
    df.to_csv(target_path, index=False)
    return {
        "columns_scaled": changed,
        "factor_mean": float(np.mean(factor)),
        "factor_min": float(np.min(factor)),
        "factor_max": float(np.max(factor)),
        "peak_ratio_max": float(max(peak_ratios)) if peak_ratios else 0.0,
        "peak_safe": bool(peak_safe),
        "peak_limit_ratio": float(peak_limit_ratio),
        "strength_scale": float(strength_scale),
    }


def _demand_file_total(path: Path) -> float:
    frame = pd.read_csv(path)
    numeric = frame.drop(columns=["period"], errors="ignore").apply(
        pd.to_numeric, errors="coerce"
    )
    return float(numeric.fillna(0.0).clip(lower=0.0).sum().sum())


def build_package(
    scenario: str,
    output_dir: Path,
    year: int = 2020,
    quick_years: int = 2,
    quick_hours: int = 168,
    policy_mode: str = "quick",
    temperature_demand: bool = True,
    temperature_peak_safe: bool = False,
    temperature_strength_scale: float = 1.0,
    year_specific_curves: bool = False,
    model_scope: str = "integrated",
    profile_scenario: str | None = None,
    temperature_offset_c: float = 0.0,
    temperature_case: str | None = None,
    temperature_calibration: dict[str, object] | None = None,
) -> ScenarioPackage:
    root = _project_root()
    data = root / "data"
    scenario_dir = output_dir / scenario
    scenario_data = scenario_dir / "data"
    scenario_data.mkdir(parents=True, exist_ok=True)

    ncre_base = data / "ncre_aval_factor_HR_2020_2050_sdewes7.csv"
    base_genco_path = data / "genco_data_HR_sdewes18.csv"
    genco_path = scenario_data / f"genco_data_{scenario}.csv"
    base_genco = pd.read_csv(base_genco_path)
    legacy_units = set(base_genco.loc[
        _technology_mask(base_genco, "WTON") | _technology_mask(base_genco, "PHOT"), "unit_name"
    ].astype(str))
    genco, genco_diagnostics = _build_climate_genco(base_genco_path, genco_path, data / "SC")
    wind_caps = _renewable_units(genco, WIND_TOKENS)
    solar_caps = _renewable_units(genco, SOLAR_TOKENS)

    if policy_mode == "quick":
        selected_years = list(range(year, year + max(2, quick_years)))
    elif policy_mode == "myopic5":
        selected_years = list(range(2020, 2051, 5))
    elif policy_mode == "original":
        setup = pd.read_excel(data / "policy14.xlsx", sheet_name="setup")
        selected_years = pd.to_numeric(setup["year"], errors="coerce").dropna().astype(int).tolist()
    else:
        raise ValueError(f"Unsupported policy_mode: {policy_mode}")

    ncre_template = _drop_legacy_columns(pd.read_csv(ncre_base), legacy_units)
    ncre_frames: list[pd.DataFrame] = []
    climate_frames: list[pd.DataFrame] = []
    year_diagnostics: dict[str, dict] = {}
    base_profile = None
    base_normal = None
    probability = 0.0
    cluster_size = 0

    years_to_generate = selected_years if year_specific_curves else [year]
    for planning_year in years_to_generate:
        profiles = _load_representative_profiles(year=planning_year)
        source_scenario = profile_scenario or scenario
        if source_scenario not in profiles:
            raise KeyError(f"Scenario {source_scenario!r} not available. Available: {sorted(profiles)}")
        profile = np.asarray(profiles[source_scenario]["profile"], dtype=float).copy()
        profile[:, 2] += float(temperature_offset_c)
        normal = np.asarray(profiles["avg_yield__normal"]["profile"], dtype=float)
        if base_profile is None:
            base_profile = profile
            base_normal = normal
            probability = float(profiles[source_scenario]["probability"])
            cluster_size = int(profiles[source_scenario]["cluster_size"])

        wind_profiles, wind_diag = _spatial_unit_profiles(
            profile[:, 1],
            data / "SC" / "HR_wind_nuts_profiles.parquet",
            data / "SC" / "HR_wind_transfer_nuts_to_units.csv",
            wind_caps,
        )
        solar_profiles, solar_diag = _spatial_unit_profiles(
            profile[:, 0],
            data / "SC" / "HR_solar_macro_profiles.parquet",
            data / "SC" / "HR_solar_transfer_macro_to_units.csv",
            solar_caps,
        )
        if wind_diag["mass_mean_abs_error"] > 1e-4 or solar_diag["mass_mean_abs_error"] > 1e-4:
            raise ValueError(
                f"Spatial mass consistency failed for {planning_year}: "
                f"wind={wind_diag['mass_mean_abs_error']:.6g}, solar={solar_diag['mass_mean_abs_error']:.6g}"
            )

        frame = ncre_template.copy()
        for column in wind_profiles.columns:
            frame[column] = wind_profiles[column].to_numpy(dtype=float)
        for column in solar_profiles.columns:
            frame[column] = solar_profiles[column].to_numpy(dtype=float)
        if year_specific_curves:
            frame.insert(0, "year", int(planning_year))
        ncre_frames.append(frame)

        climate_frames.append(pd.DataFrame({
            "year": int(planning_year),
            "period": np.arange(1, 8761),
            "solar_cf": profile[:, 0],
            "wind_cf": profile[:, 1],
            "temperature_c": profile[:, 2],
            "normal_temperature_c": normal[:, 2],
        }))
        year_diagnostics[str(planning_year)] = {"wind": wind_diag, "solar": solar_diag}

    ncre = pd.concat(ncre_frames, ignore_index=True)
    wind_cols = list(wind_caps)
    solar_cols = list(solar_caps)
    wind_mean = float(np.mean([item["wind"]["aggregate_mean_cf"] for item in year_diagnostics.values()]))
    solar_mean = float(np.mean([item["solar"]["aggregate_mean_cf"] for item in year_diagnostics.values()]))

    ncre_path = scenario_data / f"ncre_aval_factor_{scenario}.csv"
    ncre.to_csv(ncre_path, index=False)

    base_inflows_path = data / "scaled_inflows_HR_2020_2050_sdewes4.csv"
    if year_specific_curves:
        inflows_path = scenario_data / f"scaled_inflows_{scenario}.csv"
        _write_year_specific_curves(base_inflows_path, inflows_path, selected_years)
    else:
        inflows_path = base_inflows_path

    if policy_mode == "quick":
        policy_path = scenario_data / f"policy14_{scenario}_quick.xlsx"
        _write_quick_policy(
            data / "policy14.xlsx",
            policy_path,
            years=quick_years,
            hours=quick_hours,
            start_year=year,
        )
    elif policy_mode == "original":
        policy_path = scenario_data / f"policy14_{scenario}_original.xlsx"
        shutil.copy2(data / "policy14.xlsx", policy_path)
    elif policy_mode == "myopic5":
        policy_path = scenario_data / f"policy14_{scenario}_myopic5.xlsx"
        setup = pd.read_excel(data / "policy14.xlsx", sheet_name="setup")
        original_hours = int(pd.to_numeric(setup["last_hour"], errors="coerce").dropna().iloc[0])
        _write_selected_year_policy(
            data / "policy14.xlsx",
            policy_path,
            selected_years=list(range(2020, 2051, 5)),
            hours=original_hours,
        )
    else:
        raise ValueError(f"Unsupported policy_mode: {policy_mode}")

    heat_indv_path = heat_dh_path = cool_indv_path = cool_dh_path = None
    temp_notes: dict[str, dict[str, object]] = {}
    if temperature_demand:
        mappings = [
            ("heat_indv", "heat", data / "heat_demand_HR_2020_2050_sdewes_indv4.csv"),
            ("heat_dh", "heat", data / "heat_demand_HR_2020_2050_sdewes_DH2.csv"),
            ("cool_indv", "cool", data / "cool_demand_HR_2020_2050_sdewes_indv2.csv"),
            ("cool_dh", "cool", data / "cool_demand_HR_2020_2050_sdewes_DH2.csv"),
        ]
        for key, mode, src in mappings:
            dst = scenario_data / f"{src.stem}_{scenario}{src.suffix}"
            if year_specific_curves:
                shutil.copy2(src, dst)
                base_total = _demand_file_total(src)
                temp_notes[key] = {
                    "runtime_scaling": True,
                    "mode": mode,
                    "peak_safe": bool(temperature_peak_safe),
                    "strength_scale": float(temperature_strength_scale),
                    "base_total": base_total,
                    "active": bool(base_total > 0.0),
                }
            else:
                temp_notes[key] = _scale_demand_file(
                    src,
                    dst,
                    base_profile[:, 2],
                    base_normal[:, 2],
                    mode,
                    peak_safe=temperature_peak_safe,
                    strength_scale=temperature_strength_scale,
                )
                base_total = _demand_file_total(src)
                temp_notes[key]["base_total"] = base_total
                temp_notes[key]["active"] = bool(base_total > 0.0)
            if key == "heat_indv":
                heat_indv_path = dst
            elif key == "heat_dh":
                heat_dh_path = dst
            elif key == "cool_indv":
                cool_indv_path = dst
            elif key == "cool_dh":
                cool_dh_path = dst

        inactive_channels = [
            key for key, diagnostics in temp_notes.items()
            if not diagnostics.get("active", False)
        ]
        if inactive_channels:
            print(
                "[THERMAL INPUT] Zero baseline demand; temperature scaling is inactive for: "
                + ", ".join(inactive_channels)
            )

    climate_profile = pd.concat(climate_frames, ignore_index=True)
    climate_profile_path = scenario_data / f"climate_profile_{scenario}.csv"
    climate_profile.to_csv(climate_profile_path, index=False)

    policy_setup = pd.read_excel(policy_path, sheet_name="setup")
    first_hours = (
        pd.to_numeric(policy_setup["first_hour"], errors="coerce").dropna()
        if "first_hour" in policy_setup else pd.Series(dtype=float)
    )
    last_hours = (
        pd.to_numeric(policy_setup["last_hour"], errors="coerce").dropna()
        if "last_hour" in policy_setup else pd.Series(dtype=float)
    )
    solver_first_hour = int(first_hours.iloc[0]) if not first_hours.empty else 1
    solver_last_hour = int(last_hours.iloc[0]) if not last_hours.empty else 8760
    if solver_first_hour < 1 or solver_last_hour < solver_first_hour or solver_last_hour > 8760:
        raise ValueError(
            f"Invalid policy hour range: {solver_first_hour}-{solver_last_hour}; "
            "climate profiles contain 8760 hourly values per year."
        )
    solver_profile = climate_profile.loc[
        climate_profile["period"].between(solver_first_hour, solver_last_hour)
    ]
    solver_hours = solver_last_hour - solver_first_hour + 1

    summary = {
        "scenario": scenario,
        "model_scope": model_scope,
        "profile_source_scenario": profile_scenario or scenario,
        "temperature_case": temperature_case,
        "temperature_offset_c": float(temperature_offset_c),
        "temperature_stress_calibration": temperature_calibration,
        "probability_interpretation": (
            "source renewable profile share; calibrated thermal stress cases are sensitivity cases"
            if temperature_case else "representative cluster share"
        ),
        "year": year,
        "periods": int(len(ncre)),
        "quick_years": int(quick_years) if policy_mode == "quick" else None,
        "quick_hours": int(quick_hours) if policy_mode == "quick" else None,
        "policy_start_year": int(year if policy_mode == "quick" else 2020),
        "policy_mode": policy_mode,
        "policy_years": selected_years,
        "year_specific_curves": bool(year_specific_curves),
        "probability": probability,
        "cluster_size": cluster_size,
        "wind_columns": wind_cols,
        "solar_columns": solar_cols,
        "wind_resolution_selected": "nuts3",
        "active_wind_nuts3_count": int(len(wind_cols)),
        "solar_resolution_selected": "macro",
        "wind_mass_mean_abs_error": float(max(item["wind"]["mass_mean_abs_error"] for item in year_diagnostics.values())),
        "solar_mass_mean_abs_error": float(max(item["solar"]["mass_mean_abs_error"] for item in year_diagnostics.values())),
        "wind_mean_cf_written": wind_mean,
        "solar_mean_cf_written": solar_mean,
        "temperature_mean_c": float(climate_profile["temperature_c"].mean()),
        "solver_first_hour": solver_first_hour,
        "solver_last_hour": solver_last_hour,
        "solver_hours_per_year": solver_hours,
        "wind_mean_cf_solver": float(solver_profile["wind_cf"].mean()),
        "solar_mean_cf_solver": float(solver_profile["solar_cf"].mean()),
        "temperature_mean_c_solver": float(solver_profile["temperature_c"].mean()),
        "temperature_demand_scaling": bool(temperature_demand),
        "temperature_demand_runtime_scaling": bool(temperature_demand and year_specific_curves),
        "temperature_demand_peak_safe": bool(temperature_peak_safe),
        "temperature_demand_strength_scale": float(temperature_strength_scale),
        "temperature_demand_notes": temp_notes,
        "temperature_demand_active_channels": [
            key for key, diagnostics in temp_notes.items()
            if diagnostics.get("active", False)
        ],
        "ncre_path": str(ncre_path),
        "genco_path": str(genco_path),
        "inflows_path": str(inflows_path),
        "policy_path": str(policy_path),
        "climate_profile_path": str(climate_profile_path),
        "genco_diagnostics": genco_diagnostics,
        "spatial_diagnostics_by_year": year_diagnostics,
    }
    summary_path = scenario_data / f"scenario_summary_{scenario}.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    package = ScenarioPackage(scenario=scenario, root=scenario_dir, ncre_path=ncre_path, genco_path=genco_path, inflows_path=inflows_path, policy_path=policy_path, heat_indv_path=heat_indv_path, heat_dh_path=heat_dh_path, cool_indv_path=cool_indv_path, cool_dh_path=cool_dh_path, summary_path=summary_path)
    specification = {key: (str(value) if isinstance(value, Path) else value) for key, value in vars(package).items()}
    (package.root / "package_specification.json").write_text(json.dumps(specification, indent=2), encoding="utf-8")
    return package


def _rel_for_main(path: Path) -> str:
    root = _project_root().resolve()
    try:
        return "./" + path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path.resolve())


def run_package(package: ScenarioPackage, python_executable: str | None = None, timeout: int | None = None) -> int:
    python_executable = python_executable or sys.executable
    log_path = package.root / "run.log"
    summary = json.loads(package.summary_path.read_text(encoding="utf-8"))
    run_year = int(summary.get("policy_start_year", summary.get("year", 2020)))
    default_run_name = f"climate_{package.scenario}_{run_year}"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "H2RES_SCENARIO_NAME": env.get("H2RES_SCENARIO_NAME", default_run_name),
            "H2RES_AVL_FACTOR_DAT": _rel_for_main(package.ncre_path),
            "H2RES_GENCO_DAT": _rel_for_main(package.genco_path),
            "H2RES_POLICY_DAT": _rel_for_main(package.policy_path),
            "H2RES_SIMPLE_CURVES": "0" if summary.get("year_specific_curves", False) else "1",
            "H2RES_INFLOWS_DAT": _rel_for_main(package.inflows_path),
            "H2RES_CLIMATE_ENABLED": "1",
            "H2RES_CLIMATE_PREPARED_INPUTS": "1",
            "H2RES_CLIMATE_SCENARIO": package.scenario,
            "H2RES_CLIMATE_COUNTRY": "HR",
            "H2RES_CLIMATE_PLANNING_YEAR": str(run_year),
            "H2RES_CLIMATE_PROFILE_DAT": _rel_for_main(Path(summary["climate_profile_path"])),
            "H2RES_CLIMATE_SUMMARY_DAT": _rel_for_main(package.summary_path),
            "H2RES_TWO_STAGE_AUTO_MODE": env.get("H2RES_TWO_STAGE_AUTO_MODE", "1"),
            "H2RES_REPRESENTATIVE_CAPACITY_MODE": env.get("H2RES_REPRESENTATIVE_CAPACITY_MODE", "0"),
            "H2RES_FIXED_CAPACITY_MODE": env.get("H2RES_FIXED_CAPACITY_MODE", "0"),
            "H2RES_ROLLING_DISPATCH_MODE": env.get("H2RES_ROLLING_DISPATCH_MODE", "1"),
            "H2RES_ROLLING_TERMINAL_FUEL_SOC": env.get("H2RES_ROLLING_TERMINAL_FUEL_SOC", "0"),
            "H2RES_STAGE2_QUANTITY_GUIDANCE_ACTIVE": env.get("H2RES_STAGE2_QUANTITY_GUIDANCE_ACTIVE", "0"),
            "H2RES_RUN_CHUNK_EXPORTS": env.get("H2RES_RUN_CHUNK_EXPORTS", "0"),
            "H2RES_RUN_FINAL_EXPORTS": env.get("H2RES_RUN_FINAL_EXPORTS", "0"),
            "H2RES_STAGE1_EXPORT_TIMING": env.get("H2RES_STAGE1_EXPORT_TIMING", "before_stage2"),
            "H2RES_TWO_STAGE_SKIP_STAGE2": env.get("H2RES_TWO_STAGE_SKIP_STAGE2", "0"),
            "H2RES_REPRESENTATIVE_WEEK_SELECTION": env.get("H2RES_REPRESENTATIVE_WEEK_SELECTION", "profile_match"),
            "H2RES_REPRESENTATIVE_WEEK_STARTS": env.get("H2RES_REPRESENTATIVE_WEEK_STARTS", ""),
            "H2RES_MAX_MYOPIC_CHUNKS": env.get("H2RES_MAX_MYOPIC_CHUNKS", "0"),
            "H2RES_SOLVER_STAGE1_METHOD": env.get("H2RES_SOLVER_STAGE1_METHOD", "1"),
            "H2RES_ROLLING_WRITE_WINDOW_FILES": "0",
            "H2RES_SOLVER_STAGE1_THREADS": env.get("H2RES_SOLVER_STAGE1_THREADS", "4"),
            "H2RES_SOLVER_FALLBACK_THREADS": env.get("H2RES_SOLVER_FALLBACK_THREADS", "4"),
            "H2RES_STAGE1_CAPACITY_RESERVE_MARGIN": env.get(
                "H2RES_STAGE1_CAPACITY_RESERVE_MARGIN", "0.20"
            ),
            "H2RES_STAGE1_CAPACITY_RESERVE_START_YEAR": env.get(
                "H2RES_STAGE1_CAPACITY_RESERVE_START_YEAR", "2025"
            ),
        }
    )
    if summary.get("model_scope") == "electricity_only":
        env.update(
            {
                "H2RES_FLEX_TECH_DAT": "./data/flex_tech_HR_2020_2050_sdewes_regular6.xlsx",
                "H2RES_TWO_STAGE_AUTO_MODE": "0",
                "H2RES_REPRESENTATIVE_CAPACITY_MODE": "0",
                "H2RES_FIXED_CAPACITY_MODE": "0",
                "H2RES_ROLLING_DISPATCH_MODE": "0",
                "H2RES_MODEL_HEATING": "0",
                "H2RES_MODEL_COOLING": "0",
                "H2RES_MODEL_TRANSPORT_AND_INDUSTRY": "0",
                "H2RES_MODEL_EFUEL": "0",
                "H2RES_MODEL_H2_SUPPLY_DEMAND_BALANCE": "0",
                "H2RES_MODEL_OIL_GAS_PRODUCTS_SUPPLY": "0",
                "H2RES_MODEL_CO2_POST_GEN_STORAGE": "0",
            }
        )
    elif summary.get("model_scope") == "power_thermal":
        env.update(
            {
                "H2RES_FLEX_TECH_DAT": "./data/flex_tech_HR_2020_2050_sdewes_regular6.xlsx",
                "H2RES_TWO_STAGE_AUTO_MODE": "0",
                "H2RES_REPRESENTATIVE_CAPACITY_MODE": "0",
                "H2RES_FIXED_CAPACITY_MODE": "0",
                "H2RES_ROLLING_DISPATCH_MODE": "0",
                "H2RES_MODEL_HEATING": "1",
                "H2RES_MODEL_COOLING": "1",
                "H2RES_MODEL_TRANSPORT_AND_INDUSTRY": "0",
                "H2RES_MODEL_EFUEL": "0",
                "H2RES_MODEL_H2_SUPPLY_DEMAND_BALANCE": "0",
                "H2RES_MODEL_OIL_GAS_PRODUCTS_SUPPLY": "0",
                "H2RES_MODEL_CO2_POST_GEN_STORAGE": "0",
            }
        )
    if package.heat_indv_path:
        env["H2RES_HEAT_DEMAND_INDV_DAT"] = _rel_for_main(package.heat_indv_path)
    if package.heat_dh_path:
        env["H2RES_HEAT_DEMAND_DH_DAT"] = _rel_for_main(package.heat_dh_path)
    if package.cool_indv_path:
        env["H2RES_COOL_DEMAND_INDV_DAT"] = _rel_for_main(package.cool_indv_path)
    if package.cool_dh_path:
        env["H2RES_COOL_DEMAND_DH_DAT"] = _rel_for_main(package.cool_dh_path)

    start = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            [python_executable, "main.py"],
            cwd=_project_root(),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
        )
    duration = time.time() - start
    status = {
        "scenario": package.scenario,
        "return_code": proc.returncode,
        "duration_sec": duration,
        "log_path": str(log_path),
        "results_dir": str(_project_root() / "results" / env["H2RES_SCENARIO_NAME"]),
    }
    (package.root / "run_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="avg_yield__normal")
    parser.add_argument("--all-scenarios", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=_project_root() / "climate_newshare_runs")
    parser.add_argument("--year", type=int, default=2020)
    parser.add_argument("--quick-years", type=int, default=2)
    parser.add_argument("--quick-hours", type=int, default=168)
    parser.add_argument("--policy-mode", choices=("quick", "original", "myopic5"), default="myopic5")
    parser.add_argument("--no-temperature-demand", action="store_true")
    parser.add_argument(
        "--electricity-only",
        action="store_true",
        help=(
            "Run a controlled power-sector experiment with heating, cooling, "
            "transport, industry, e-fuels, hydrogen and CO2 storage disabled."
        ),
    )
    parser.add_argument(
        "--power-thermal-only",
        action="store_true",
        help=(
            "Run electricity with heating, cooling and temperature-sensitive heat-pump COP, "
            "while disabling transport, industry, e-fuels, hydrogen and CO2 storage."
        ),
    )
    parser.add_argument(
        "--thermal-sensitivity",
        action="store_true",
        help=(
            "Generate cold, normal and hot P10/P50/P90 temperature-stress cases using the same "
            "average-yield renewable profile. Implies --power-thermal-only."
        ),
    )
    parser.add_argument(
        "--thermal-case",
        choices=("cold", "normal", "hot"),
        default=None,
        help=(
            "Generate and optionally run one calibrated temperature-stress case using the "
            "average-yield renewable profile. Implies --power-thermal-only."
        ),
    )
    parser.add_argument("--temperature-demand-peak-safe", action="store_true")
    parser.add_argument("--temperature-demand-strength-scale", type=float, default=1.0)
    parser.add_argument(
        "--year-specific-curves",
        action="store_true",
        help="Generate separate renewable and inflow curves for each planning year. The default preserves NewShare simple-curves behavior.",
    )
    parser.add_argument(
        "--representative-week-selection",
        choices=("even", "profile_match", "full_year"),
        default="profile_match",
        help="Use profile_match for NewShare climate runs; even keeps the original compact mode, full_year is exact but memory-heavy.",
    )
    parser.add_argument(
        "--representative-week-starts",
        default="",
        help="Comma-separated fixed representative week starts. Use this to keep the same representative weeks across scenarios.",
    )
    parser.add_argument(
        "--max-myopic-chunks",
        type=int,
        default=None,
        help="Optional test limiter. 0 or omitted runs all chunks supported by the selected policy file.",
    )
    parser.add_argument(
        "--resume-through-year",
        type=int,
        default=None,
        help="Resume a myopic run after this completed planning year using existing verified result handoffs.",
    )
    parser.add_argument(
        "--solver-method",
        choices=("auto", "dual", "barrier"),
        default=None,
        help="Optional Stage-1 solver override. Barrier is multi-threaded; dual simplex is single-threaded.",
    )
    parser.add_argument(
        "--solver-crossover",
        choices=("auto", "off", "on"),
        default=None,
        help="Optional barrier crossover override. Use off on memory-constrained machines.",
    )
    parser.add_argument("--solver-threads", type=int, default=None)
    parser.add_argument(
        "--capacity-reserve-margin",
        type=float,
        default=0.20,
        help="Stage-1 capacity headroom applied from --capacity-reserve-start-year onward.",
    )
    parser.add_argument(
        "--capacity-reserve-start-year",
        type=int,
        default=2025,
        help="First planning year in which the Stage-1 capacity reserve is enforced.",
    )
    parser.add_argument(
        "--solver-start-file",
        type=Path,
        default=None,
        help="Optional compatible Gurobi .sol/.mst/.bas start file.",
    )
    parser.add_argument(
        "--write-solver-solution",
        type=Path,
        default=None,
        help="Optional .sol output for warming a compatible later scenario run.",
    )
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--stage1-only", action="store_true")
    parser.add_argument("--python", dest="python_executable", default=sys.executable)
    parser.add_argument("--timeout", type=int, default=None)
    args = parser.parse_args()

    if args.electricity_only and (
        args.power_thermal_only or args.thermal_sensitivity or args.thermal_case
    ):
        parser.error("--electricity-only cannot be combined with a power-thermal mode")
    if args.thermal_sensitivity and args.all_scenarios:
        parser.error("--thermal-sensitivity already defines its three cases")
    if args.thermal_case and (args.thermal_sensitivity or args.all_scenarios):
        parser.error("--thermal-case cannot be combined with --thermal-sensitivity or --all-scenarios")

    thermal_calibration = None
    if args.thermal_sensitivity or args.thermal_case:
        offsets, thermal_calibration = _training_temperature_stress_offsets(
            _project_root() / "data" / "SC" / "HR_data.parquet"
        )
        thermal_cases = (args.thermal_case,) if args.thermal_case else ("cold", "normal", "hot")
        run_specs = [
            {
                "scenario": f"avg_yield__thermal_{case}",
                "profile_scenario": "avg_yield__normal",
                "temperature_case": case,
                "temperature_offset_c": offsets[case],
            }
            for case in thermal_cases
        ]
    else:
        scenarios = SCENARIOS if args.all_scenarios else (args.scenario,)
        run_specs = [
            {
                "scenario": scenario,
                "profile_scenario": None,
                "temperature_case": None,
                "temperature_offset_c": 0.0,
            }
            for scenario in scenarios
        ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    status = 0
    os.environ["H2RES_REPRESENTATIVE_WEEK_SELECTION"] = args.representative_week_selection
    if args.representative_week_starts:
        os.environ["H2RES_REPRESENTATIVE_WEEK_STARTS"] = args.representative_week_starts
    if args.max_myopic_chunks is not None:
        os.environ["H2RES_MAX_MYOPIC_CHUNKS"] = str(args.max_myopic_chunks)
    if args.resume_through_year is not None:
        os.environ["H2RES_RESUME_THROUGH_YEAR"] = str(args.resume_through_year)
    if args.solver_method is not None:
        os.environ["H2RES_SOLVER_STAGE1_METHOD"] = {
            "auto": "-1",
            "dual": "1",
            "barrier": "2",
        }[args.solver_method]
    if args.solver_crossover is not None:
        os.environ["H2RES_SOLVER_STAGE1_CROSSOVER"] = {
            "auto": "-1",
            "off": "0",
            "on": "1",
        }[args.solver_crossover]
    if args.solver_threads is not None:
        if args.solver_threads <= 0:
            parser.error("--solver-threads must be positive")
        os.environ["H2RES_SOLVER_STAGE1_THREADS"] = str(args.solver_threads)
        os.environ["H2RES_SOLVER_FALLBACK_THREADS"] = str(args.solver_threads)
    if args.capacity_reserve_margin < 0:
        parser.error("--capacity-reserve-margin must be non-negative")
    os.environ["H2RES_STAGE1_CAPACITY_RESERVE_MARGIN"] = str(args.capacity_reserve_margin)
    os.environ["H2RES_STAGE1_CAPACITY_RESERVE_START_YEAR"] = str(
        args.capacity_reserve_start_year
    )
    if args.solver_start_file is not None:
        os.environ["H2RES_SOLVER_STAGE1_START_FILE"] = str(args.solver_start_file.resolve())
        os.environ["H2RES_SOLVER_STAGE1_LP_WARM_START"] = "2"
    if args.write_solver_solution is not None:
        os.environ["H2RES_SOLVER_STAGE1_WRITE_SOLUTION_FILE"] = str(
            args.write_solver_solution.resolve()
        )
    if args.stage1_only:
        os.environ["H2RES_TWO_STAGE_SKIP_STAGE2"] = "1"
    for spec in run_specs:
        scenario = spec["scenario"]
        model_scope = (
            "electricity_only" if args.electricity_only
            else "power_thermal" if (
                args.power_thermal_only or args.thermal_sensitivity or args.thermal_case
            )
            else "integrated"
        )
        package = build_package(
            scenario=scenario,
            output_dir=args.output_dir,
            year=args.year,
            quick_years=args.quick_years,
            quick_hours=args.quick_hours,
            policy_mode=args.policy_mode,
            temperature_demand=not args.no_temperature_demand and model_scope != "electricity_only",
            temperature_peak_safe=args.temperature_demand_peak_safe,
            temperature_strength_scale=args.temperature_demand_strength_scale,
            year_specific_curves=args.year_specific_curves,
            model_scope=model_scope,
            profile_scenario=spec["profile_scenario"],
            temperature_offset_c=spec["temperature_offset_c"],
            temperature_case=spec["temperature_case"],
            temperature_calibration=thermal_calibration,
        )
        row = json.loads(package.summary_path.read_text(encoding="utf-8"))
        row["capacity_reserve_margin"] = float(args.capacity_reserve_margin)
        row["capacity_reserve_start_year"] = int(args.capacity_reserve_start_year)
        package.summary_path.write_text(json.dumps(row, indent=2), encoding="utf-8")
        if args.run:
            row["return_code"] = run_package(package, args.python_executable, args.timeout)
            status = max(status, int(row["return_code"]))
        rows.append(row)
    pd.DataFrame(rows).to_csv(args.output_dir / "scenario_packages_summary.csv", index=False)
    print(f"Wrote scenario packages to {args.output_dir}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())

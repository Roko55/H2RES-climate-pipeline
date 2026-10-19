"""Temperature-to-demand coupling utilities for rolling-horizon H2RES runs.

The climate scenario manager provides one synthetic temperature profile per
scenario year. This module converts that profile into runtime updates for:

- district heat demand
- general heat demand
- cooling demand
- air-to-water heat-pump COP

The default implementation keeps the annual heat and cooling totals equal to
the original H2RES input files, while redistributing those totals across hours
according to the generated temperature profile. Annual-total scaling remains
available as an explicit sensitivity option.
"""

from __future__ import annotations

import os
from typing import Iterable, Mapping, MutableMapping, Sequence, Tuple

import numpy as np
import pandas as pd


DemandKey = Tuple[str, int, int]
CopKey = Tuple[str, int, int]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data", "SC")

_baseline_temp_cache: dict[str, np.ndarray] = {}


def _as_hourly_array(values: Sequence[float], n_hours: int) -> np.ndarray:
    arr = np.asarray(values, dtype=float).reshape(-1)
    if arr.size == 0:
        return np.zeros(n_hours, dtype=float)
    if arr.size < n_hours:
        arr = np.pad(arr, (0, n_hours - arr.size), mode="edge")
    arr = arr[:n_hours]
    if np.isnan(arr).any():
        mean_val = float(np.nanmean(arr)) if not np.isnan(arr).all() else 0.0
        arr = np.where(np.isnan(arr), mean_val, arr)
    return arr.astype(float)


def _baseline_series(baseline: Mapping[Tuple[str, int, int], float],
                     unit: str,
                     year: int,
                     periods: Sequence[int]) -> np.ndarray:
    return np.array(
        [float(baseline.get((unit, int(period), int(year)), 0.0)) for period in periods],
        dtype=float,
    )


def _hourly_mean_8760(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(index=np.arange(8760))

    work = df.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        if "time" in work.columns:
            work["time"] = pd.to_datetime(work["time"], errors="coerce")
            work = work.dropna(subset=["time"]).set_index("time")
        else:
            work.index = pd.to_datetime(work.index, errors="coerce")

    work = work[~work.index.isna()].sort_index()
    if work.index.tz is not None:
        work.index = work.index.tz_localize(None)

    work = work.select_dtypes(include=[np.number]).copy()
    if work.empty:
        return pd.DataFrame(index=np.arange(8760))

    idx = work.index
    leap_day = (idx.month == 2) & (idx.day == 29)
    work = work.loc[~leap_day]
    idx = work.index

    is_leap = idx.is_leap_year.astype(int)
    after_feb_28 = ((idx.month > 2) | ((idx.month == 2) & (idx.day > 28))).astype(int)
    adj_doy = idx.dayofyear - (is_leap * after_feb_28)
    hour_of_year = (adj_doy - 1) * 24 + idx.hour
    work = work.assign(hour_of_year=hour_of_year.values)

    hourly = work.groupby("hour_of_year").mean(numeric_only=True)
    hourly = hourly.reindex(np.arange(8760))
    hourly = hourly.interpolate(limit_direction="both").ffill().bfill()
    return hourly


def _load_country_baseline_temp_profile(country_code: str) -> np.ndarray:
    cc = str(country_code or "HR").upper()
    if cc in _baseline_temp_cache:
        return _baseline_temp_cache[cc]

    data_path = os.path.join(DATA_DIR, f"{cc}_data.parquet")
    if not os.path.exists(data_path):
        baseline = np.zeros(8760, dtype=float)
        _baseline_temp_cache[cc] = baseline
        return baseline

    df = pd.read_parquet(data_path)
    hourly = _hourly_mean_8760(df)
    if "temperature" not in hourly.columns:
        baseline = np.zeros(8760, dtype=float)
    else:
        baseline = hourly["temperature"].values.astype(float)
    _baseline_temp_cache[cc] = baseline
    return baseline


def _degree_day_signal(temp_c: np.ndarray, base_c: float, mode: str) -> np.ndarray:
    if mode == "heating":
        return np.maximum(float(base_c) - temp_c, 0.0)
    if mode == "cooling":
        return np.maximum(temp_c - float(base_c), 0.0)
    raise ValueError(f"Unsupported degree-day mode: {mode}")


def _normalized_shape(signal: np.ndarray) -> np.ndarray:
    total = float(np.sum(signal))
    if total <= 1e-12:
        return np.full(signal.size, 1.0 / max(signal.size, 1), dtype=float)
    return signal / total


def _annual_scale_factor(current_signal_sum: float,
                         baseline_signal_sum: float,
                         elasticity: float,
                         enabled: bool) -> float:
    if not enabled or baseline_signal_sum <= 1e-12:
        return 1.0
    ratio = float(current_signal_sum) / float(baseline_signal_sum)
    return max(0.0, 1.0 + float(elasticity) * (ratio - 1.0))


def _reshape_with_target_total(baseline_values: np.ndarray,
                               target_total: float,
                               shape: np.ndarray,
                               blend: float) -> np.ndarray:
    if target_total <= 0.0:
        return np.zeros_like(baseline_values, dtype=float)

    baseline_total = float(np.nansum(baseline_values))
    if baseline_total <= 0.0:
        return np.maximum(target_total * shape, 0.0)

    weather_values = target_total * shape
    baseline_scaled = baseline_values * (target_total / baseline_total)
    blend = float(np.clip(blend, 0.0, 1.0))
    reshaped = (1.0 - blend) * baseline_scaled + blend * weather_values

    reshaped_total = float(np.nansum(reshaped))
    if reshaped_total > 0.0:
        reshaped *= target_total / reshaped_total
    return np.maximum(reshaped, 0.0)


def _apply_demand_coupling(target: MutableMapping[DemandKey, float],
                           baseline: Mapping[DemandKey, float],
                           units: Iterable[str],
                           year: int,
                           periods: Sequence[int],
                           shape: np.ndarray,
                           current_signal: np.ndarray,
                           baseline_signal: np.ndarray,
                           blend: float,
                           elasticity: float,
                           scale_annual_totals: bool) -> tuple[list[str], dict[str, float], dict[str, float], dict[str, float]]:
    updated_units = []
    scale_factors = {}
    baseline_totals = {}
    updated_totals = {}

    current_signal_sum = float(np.sum(current_signal))
    baseline_signal_sum = float(np.sum(baseline_signal))
    annual_scale = _annual_scale_factor(
        current_signal_sum=current_signal_sum,
        baseline_signal_sum=baseline_signal_sum,
        elasticity=elasticity,
        enabled=scale_annual_totals,
    )

    for unit in units:
        keys = [(unit, int(period), int(year)) for period in periods]
        if not any(key in baseline for key in keys):
            continue

        base_values = _baseline_series(baseline, unit, year, periods)
        base_total = float(np.nansum(base_values))
        target_total = base_total * annual_scale
        new_values = _reshape_with_target_total(base_values, target_total, shape, blend)
        for key, value in zip(keys, new_values):
            if key in target:
                target[key] = float(value)
        updated_units.append(unit)
        scale_factors[unit] = annual_scale
        baseline_totals[unit] = base_total
        updated_totals[unit] = float(np.nansum(new_values))

    return updated_units, scale_factors, baseline_totals, updated_totals


def _theoretical_cop(temp_c: np.ndarray,
                     sink_temp_c: float,
                     carnot_efficiency: float,
                     min_cop: float,
                     max_cop: float) -> np.ndarray:
    sink_k = float(sink_temp_c) + 273.15
    source_k = temp_c + 273.15
    lift_k = np.maximum(sink_k - source_k, 5.0)
    raw = float(carnot_efficiency) * sink_k / lift_k
    return np.clip(raw, float(min_cop), float(max_cop))


def _temperature_sensitive_cop(temp_c: np.ndarray,
                               baseline_temp_c: np.ndarray,
                               baseline_values: np.ndarray,
                               sink_temp_c: float,
                               carnot_efficiency: float,
                               min_cop: float,
                               max_cop: float,
                               cop_temperature_sensitivity: float,
                               calibrate_to_baseline_mean: bool) -> np.ndarray:
    ref_theoretical = _theoretical_cop(
        baseline_temp_c, sink_temp_c, carnot_efficiency, min_cop, max_cop
    )
    cur_theoretical = _theoretical_cop(
        temp_c, sink_temp_c, carnot_efficiency, min_cop, max_cop
    )

    ratio = np.where(ref_theoretical > 1e-9, cur_theoretical / ref_theoretical, 1.0)
    ratio = np.clip(ratio, 0.25, 4.0)
    new_cop = baseline_values * np.power(ratio, float(cop_temperature_sensitivity))

    if calibrate_to_baseline_mean:
        baseline_mean = float(np.nanmean(baseline_values))
        new_mean = float(np.nanmean(new_cop))
        if baseline_mean > 0.0 and new_mean > 0.0:
            new_cop *= baseline_mean / new_mean

    return np.clip(new_cop, float(min_cop), float(max_cop))


def apply_temperature_coupling_to_runtime(
    temp_profile: Sequence[float],
    year: int,
    country_code: str,
    periods: Sequence[int],
    heat_demand: MutableMapping[DemandKey, float],
    cooling_demand: MutableMapping[DemandKey, float],
    hp_cop_period: MutableMapping[CopKey, float],
    baseline_heat_demand: Mapping[DemandKey, float],
    baseline_cooling_demand: Mapping[DemandKey, float],
    baseline_hp_cop_period: Mapping[CopKey, float],
    district_heat_units: Iterable[str] = ("TE-TOZagreb",),
    general_heat_units: Iterable[str] = ("general_demand",),
    cooling_units: Iterable[str] = ("TE-TOZagreb", "general_cooling"),
    atw_cop_units: Iterable[str] = ("ATW_HP",),
    heating_base_c: float = 18.0,
    cooling_base_c: float = 22.0,
    demand_shape_blend: float = 1.0,
    scale_annual_totals: bool = False,
    district_heat_elasticity: float = 0.9,
    general_heat_elasticity: float = 0.7,
    cooling_elasticity: float = 0.95,
    sink_temp_c: float = 45.0,
    carnot_efficiency: float = 0.36,
    min_cop: float = 1.2,
    max_cop: float = 5.5,
    cop_temperature_sensitivity: float = 0.85,
    calibrate_cop_to_baseline_mean: bool = True,
) -> dict:
    """
    Update H2RES heat/cooling/COP dictionaries using a climate temperature year.

    The default mode follows the conservative integration requested for H2RES:

    - district heat keeps its original annual total but follows HDD shape
    - general heat keeps its original annual total but follows HDD shape
    - cooling keeps its original annual total but follows CDD shape
    - heat-pump COP becomes temperature-sensitive against a climatological baseline

    Set ``scale_annual_totals=True`` only for explicit thermal-stress
    sensitivity runs where annual heat/cooling demand should vary.
    """
    periods = [int(period) for period in periods[:8760]]
    temp_c = _as_hourly_array(temp_profile, len(periods))
    baseline_temp_c = _as_hourly_array(
        _load_country_baseline_temp_profile(country_code),
        len(periods),
    )

    heating_signal = _degree_day_signal(temp_c, heating_base_c, mode="heating")
    cooling_signal = _degree_day_signal(temp_c, cooling_base_c, mode="cooling")
    baseline_heating_signal = _degree_day_signal(baseline_temp_c, heating_base_c, mode="heating")
    baseline_cooling_signal = _degree_day_signal(baseline_temp_c, cooling_base_c, mode="cooling")

    heating_shape = _normalized_shape(heating_signal)
    cooling_shape = _normalized_shape(cooling_signal)

    district_heat_updated, district_heat_scales, district_heat_base_totals, district_heat_updated_totals = _apply_demand_coupling(
        target=heat_demand,
        baseline=baseline_heat_demand,
        units=district_heat_units,
        year=int(year),
        periods=periods,
        shape=heating_shape,
        current_signal=heating_signal,
        baseline_signal=baseline_heating_signal,
        blend=demand_shape_blend,
        elasticity=district_heat_elasticity,
        scale_annual_totals=scale_annual_totals,
    )
    general_heat_updated, general_heat_scales, general_heat_base_totals, general_heat_updated_totals = _apply_demand_coupling(
        target=heat_demand,
        baseline=baseline_heat_demand,
        units=general_heat_units,
        year=int(year),
        periods=periods,
        shape=heating_shape,
        current_signal=heating_signal,
        baseline_signal=baseline_heating_signal,
        blend=demand_shape_blend,
        elasticity=general_heat_elasticity,
        scale_annual_totals=scale_annual_totals,
    )
    cooling_updated, cooling_scales, cooling_base_totals, cooling_updated_totals = _apply_demand_coupling(
        target=cooling_demand,
        baseline=baseline_cooling_demand,
        units=cooling_units,
        year=int(year),
        periods=periods,
        shape=cooling_shape,
        current_signal=cooling_signal,
        baseline_signal=baseline_cooling_signal,
        blend=demand_shape_blend,
        elasticity=cooling_elasticity,
        scale_annual_totals=scale_annual_totals,
    )

    cop_updated = []
    for unit in atw_cop_units:
        keys = [(unit, int(period), int(year)) for period in periods]
        if not any(key in baseline_hp_cop_period for key in keys):
            continue

        baseline_values = np.array(
            [float(baseline_hp_cop_period.get(key, np.nan)) for key in keys],
            dtype=float,
        )
        if np.isnan(baseline_values).all():
            continue
        baseline_values = np.where(
            np.isnan(baseline_values),
            float(np.nanmean(baseline_values)),
            baseline_values,
        )
        new_cop = _temperature_sensitive_cop(
            temp_c=temp_c,
            baseline_temp_c=baseline_temp_c,
            baseline_values=baseline_values,
            sink_temp_c=sink_temp_c,
            carnot_efficiency=carnot_efficiency,
            min_cop=min_cop,
            max_cop=max_cop,
            cop_temperature_sensitivity=cop_temperature_sensitivity,
            calibrate_to_baseline_mean=calibrate_cop_to_baseline_mean,
        )
        for key, value in zip(keys, new_cop):
            if key in hp_cop_period:
                hp_cop_period[key] = float(value)
        cop_updated.append(unit)

    return {
        "year": int(year),
        "country_code": str(country_code).upper(),
        "temp_mean_c": float(np.mean(temp_c)),
        "temp_min_c": float(np.min(temp_c)),
        "temp_max_c": float(np.max(temp_c)),
        "baseline_temp_mean_c": float(np.mean(baseline_temp_c)),
        "heating_base_c": float(heating_base_c),
        "cooling_base_c": float(cooling_base_c),
        "demand_shape_blend": float(demand_shape_blend),
        "scale_annual_totals": bool(scale_annual_totals),
        "scenario_hdd_sum": float(np.sum(heating_signal)),
        "baseline_hdd_sum": float(np.sum(baseline_heating_signal)),
        "scenario_cdd_sum": float(np.sum(cooling_signal)),
        "baseline_cdd_sum": float(np.sum(baseline_cooling_signal)),
        "district_heat_units_updated": district_heat_updated,
        "general_heat_units_updated": general_heat_updated,
        "cooling_units_updated": cooling_updated,
        "district_heat_scale_factors": district_heat_scales,
        "general_heat_scale_factors": general_heat_scales,
        "cooling_scale_factors": cooling_scales,
        "district_heat_baseline_totals": district_heat_base_totals,
        "district_heat_updated_totals": district_heat_updated_totals,
        "general_heat_baseline_totals": general_heat_base_totals,
        "general_heat_updated_totals": general_heat_updated_totals,
        "cooling_baseline_totals": cooling_base_totals,
        "cooling_updated_totals": cooling_updated_totals,
        "cop_units_updated": cop_updated,
        "cop_temperature_sensitivity": float(cop_temperature_sensitivity),
    }

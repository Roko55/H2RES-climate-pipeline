"""Runtime climate input layer for the current H2RES_new_share model.

The latest shared model keeps its own genco and availability schema. This
module therefore does not replace solver units. It reads the generated climate
scenario ensemble and maps the selected scenario onto the existing HR wind and
solar availability profiles after `read_process_data.py` has built the model
input dictionaries.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OMP_NUM_THREADS", "1")

try:
    from sklearn.cluster import KMeans
except Exception:  # pragma: no cover - fallback for minimal environments
    KMeans = None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLIMATE_DATA_DIR = PROJECT_ROOT / "data" / "SC"
MODEL_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SCENARIO_FILE = CLIMATE_DATA_DIR / "xgb_synthetic_scenarios.npy"

CLIMATE_CHANGE = {
    "base_year": 2020,
    "target_year": 2050,
    "solar_factor_2050": 1.03,
    "wind_factor_2050": 0.92,
    "temp_offset_2050": 1.5,
}

DIAGONAL_SCENARIOS = ("low_yield__cold", "avg_yield__normal", "high_yield__hot")


@dataclass
class ClimateApplyResult:
    scenario: str
    probability: float
    cluster_size: int
    solar_units: list[str]
    wind_units: list[str]
    solar_mass_error: float
    wind_mass_error: float
    temperature_mean_c: float
    cop_units: list[str] = field(default_factory=list)
    cop_mean: float = math.nan
    prepared_inputs: bool = False


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.environ.get(name, default)
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _as_hourly_profile(values: np.ndarray, n_hours: int = 8760) -> np.ndarray:
    arr = np.asarray(values, dtype=float).reshape(-1)
    if _env_flag("H2RES_STRICT_CLIMATE_INPUTS"):
        if arr.size != n_hours or not np.isfinite(arr).all():
            raise ValueError("Strict climate inputs require a complete finite hourly profile")
        return arr
    if arr.size == 0:
        return np.zeros(n_hours, dtype=float)
    if arr.size < n_hours:
        arr = np.pad(arr, (0, n_hours - arr.size), mode="edge")
    arr = arr[:n_hours]
    if np.isnan(arr).any():
        fill = float(np.nanmean(arr)) if not np.isnan(arr).all() else 0.0
        arr = np.where(np.isnan(arr), fill, arr)
    return arr



def _load_synthetic_scenarios(path: Path = DEFAULT_SCENARIO_FILE) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(f"Missing synthetic climate scenario file: {path}")
    arr = np.load(path, allow_pickle=False)
    if arr.ndim != 3 or arr.shape[0] < 1 or arr.shape[1:] != (8760, 3):
        raise ValueError(f"Expected synthetic scenarios shaped (n, 8760, 3), got {arr.shape}")
    if not np.issubdtype(arr.dtype, np.number) or not np.isfinite(arr).all():
        raise ValueError("Synthetic climate scenarios must be finite numeric values")
    if ((arr[:, :, :2] < 0) | (arr[:, :, :2] > 1)).any():
        raise ValueError("Synthetic solar and wind capacity factors must be within [0, 1]")
    return arr.astype(float)



def _apply_climate_change(profile: np.ndarray, planning_year: int) -> np.ndarray:
    out = np.asarray(profile, dtype=float).copy()
    base = float(CLIMATE_CHANGE["base_year"])
    target = float(CLIMATE_CHANGE["target_year"])
    frac = 0.0 if target == base else (float(planning_year) - base) / (target - base)
    frac = max(0.0, min(1.0, frac))
    out[:, 0] *= 1.0 + frac * (float(CLIMATE_CHANGE["solar_factor_2050"]) - 1.0)
    out[:, 1] *= 1.0 + frac * (float(CLIMATE_CHANGE["wind_factor_2050"]) - 1.0)
    out[:, 2] += frac * float(CLIMATE_CHANGE["temp_offset_2050"])
    out[:, 0] = np.clip(out[:, 0], 0.0, 1.0)
    out[:, 1] = np.clip(out[:, 1], 0.0, 1.0)
    return out


def _cluster_axis(values: np.ndarray, names: tuple[str, str, str]) -> dict[str, np.ndarray]:
    values = np.asarray(values, dtype=float).reshape(-1)
    n = values.size
    if n < 3:
        return {name: np.arange(n) for name in names}

    if KMeans is not None:
        model = KMeans(n_clusters=3, random_state=42, n_init=10)
        labels = model.fit_predict(values.reshape(-1, 1))
        centers = model.cluster_centers_.reshape(-1)
        ordered_labels = [label for label, _ in sorted(enumerate(centers), key=lambda item: item[1])]
        return {name: np.where(labels == label)[0] for name, label in zip(names, ordered_labels)}

    q1, q2 = np.quantile(values, [1 / 3, 2 / 3])
    return {
        names[0]: np.where(values <= q1)[0],
        names[1]: np.where((values > q1) & (values <= q2))[0],
        names[2]: np.where(values > q2)[0],
    }


def _thermal_index(profile: np.ndarray) -> np.ndarray:
    temp = profile[:, :, 2]
    heat = np.maximum(18.0 - temp, 0.0).sum(axis=1)
    cool = np.maximum(temp - 22.0, 0.0).sum(axis=1)
    heat_z = (heat - heat.mean()) / (heat.std() if heat.std() > 0 else 1.0)
    cool_z = (cool - cool.mean()) / (cool.std() if cool.std() > 0 else 1.0)
    return cool_z - heat_z



def _representative_index(indices: np.ndarray, score: np.ndarray) -> int:
    if indices.size == 0:
        return int(np.argmin(np.abs(score - np.median(score))))
    center = float(np.mean(score[indices]))
    return int(indices[np.argmin(np.abs(score[indices] - center))])


def get_representative_scenarios(planning_year: int = 2020) -> dict[str, dict[str, Any]]:
    scenarios = _load_synthetic_scenarios()
    scenarios = np.stack([_apply_climate_change(profile, planning_year) for profile in scenarios])

    renewable_energy = scenarios[:, :, 0].sum(axis=1) + scenarios[:, :, 1].sum(axis=1)
    thermal = _thermal_index(scenarios)
    n = scenarios.shape[0]

    yield_clusters = _cluster_axis(renewable_energy, ("low_yield", "avg_yield", "high_yield"))
    thermal_clusters = _cluster_axis(thermal, ("cold", "normal", "hot"))

    out: dict[str, dict[str, Any]] = {}
    for name, idx in yield_clusters.items():
        rep = _representative_index(idx, renewable_energy)
        out[name] = {
            "profile": scenarios[rep],
            "probability": float(len(idx) / n),
            "cluster_size": int(len(idx)),
            "energy": float(renewable_energy[rep]),
            "thermal_index": float(thermal[rep]),
        }

    for name, idx in thermal_clusters.items():
        rep = _representative_index(idx, thermal)
        out[name] = {
            "profile": scenarios[rep],
            "probability": float(len(idx) / n),
            "cluster_size": int(len(idx)),
            "energy": float(renewable_energy[rep]),
            "thermal_index": float(thermal[rep]),
        }

    for yield_name in ("low_yield", "avg_yield", "high_yield"):
        for thermal_name in ("cold", "normal", "hot"):
            scenario_name = f"{yield_name}__{thermal_name}"
            idx = np.intersect1d(yield_clusters[yield_name], thermal_clusters[thermal_name])
            if idx.size == 0:
                # Fallback to the sample closest to both cluster centers.
                y_center = np.mean(renewable_energy[yield_clusters[yield_name]])
                t_center = np.mean(thermal[thermal_clusters[thermal_name]])
                y_scale = np.std(renewable_energy) or 1.0
                t_scale = np.std(thermal) or 1.0
                combined_score = ((renewable_energy - y_center) / y_scale) ** 2 + ((thermal - t_center) / t_scale) ** 2
                rep = int(np.argmin(combined_score))
            else:
                combined_score = renewable_energy + thermal * 0.0
                rep = _representative_index(idx, combined_score)

            out[scenario_name] = {
                "profile": scenarios[rep],
                "probability": float(idx.size / n),
                "cluster_size": int(idx.size),
                "energy": float(renewable_energy[rep]),
                "thermal_index": float(thermal[rep]),
            }

    return out


def _period_indices(periods: list[int], n_hours: int) -> np.ndarray:
    return np.array([(int(p) - 1) % n_hours for p in periods], dtype=int)


def _availability_units(availability: dict[Any, float]) -> set[str]:
    units: set[str] = set()
    for key in availability.keys():
        if isinstance(key, tuple) and key:
            units.add(str(key[0]))
    return units


def _read_genco(path: str | os.PathLike[str]) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in ("unit_name", "technology", "System"):
        if col not in df.columns:
            raise ValueError(f"Missing required genco column '{col}' in {path}")
    if "cap_mw" not in df.columns:
        raise ValueError(f"Missing required genco column 'cap_mw' in {path}")
    return df


def _select_units(genco: pd.DataFrame, availability: dict[Any, float], country_code: str, technology: str) -> tuple[list[str], dict[str, float]]:
    available_units = _availability_units(availability)
    work = genco.copy()
    work["System"] = work["System"].astype(str).str.upper()
    work["technology"] = work["technology"].astype(str).str.upper()
    work["unit_name"] = work["unit_name"].astype(str)
    work["cap_mw"] = pd.to_numeric(work["cap_mw"], errors="coerce").fillna(0.0)
    if "max_cap" in work.columns:
        work["max_cap"] = pd.to_numeric(work["max_cap"], errors="coerce").fillna(0.0)
    else:
        work["max_cap"] = 0.0

    mask = (
        (work["System"] == country_code.upper())
        & (work["technology"] == technology.upper())
        & (work["unit_name"].isin(available_units))
        & ((work["cap_mw"] > 0.0) | (work["max_cap"] > 0.0))
    )
    selected = work.loc[mask, ["unit_name", "cap_mw", "max_cap"]].drop_duplicates("unit_name")
    units = selected["unit_name"].tolist()
    capacities = {
        row.unit_name: float(row.cap_mw if row.cap_mw > 0 else max(row.max_cap, 1.0))
        for row in selected.itertuples(index=False)
    }
    return units, capacities


def _get_availability(availability: dict[Any, float], unit: str, period: int, years: list[int]) -> float:
    if (unit, period) in availability:
        return float(availability[(unit, period)])
    for year in years:
        if (unit, period, int(year)) in availability:
            return float(availability[(unit, period, int(year))])
    return 0.0


def _set_availability(availability: dict[Any, float], unit: str, period: int, years: list[int], value: float) -> None:
    value = float(np.clip(value, 0.0, 1.0))
    if (unit, period) in availability:
        availability[(unit, period)] = value
        return
    written = False
    for year in years:
        key = (unit, period, int(year))
        if key in availability:
            availability[key] = value
            written = True
    if not written:
        availability[(unit, period)] = value


def _scale_unit_profiles(
    availability: dict[Any, float],
    units: list[str],
    capacities: dict[str, float],
    periods: list[int],
    years: list[int],
    target_profile: np.ndarray,
) -> float:
    if not units:
        return math.nan

    matrix = np.array(
        [[_get_availability(availability, unit, int(period), years) for period in periods] for unit in units],
        dtype=float,
    )
    caps = np.array([max(float(capacities.get(unit, 1.0)), 1e-9) for unit in units], dtype=float)
    weights = caps / caps.sum()
    baseline_aggregate = weights @ matrix
    target = np.asarray(target_profile, dtype=float).reshape(-1)

    ratio = np.ones_like(target)
    valid = baseline_aggregate > 1e-8
    ratio[valid] = target[valid] / baseline_aggregate[valid]
    scaled = matrix * ratio.reshape(1, -1)
    scaled[:, ~valid] = target[~valid]
    scaled = np.clip(scaled, 0.0, 1.0)

    for row_idx, unit in enumerate(units):
        for col_idx, period in enumerate(periods):
            _set_availability(availability, unit, int(period), years, float(scaled[row_idx, col_idx]))

    aggregate_after = weights @ scaled
    return float(np.mean(np.abs(aggregate_after - target)))


def _baseline_temperature_profile() -> np.ndarray:
    data_path = CLIMATE_DATA_DIR / "HR_data.parquet"
    if not data_path.exists():
        return np.full(8760, 11.0, dtype=float)
    df = pd.read_parquet(data_path, columns=["time", "temperature"])
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).set_index("time").sort_index()
    df = df.loc[df.index.year <= 2015].copy()
    if df.empty:
        raise ValueError("No training-period temperature reference available")
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~((df.index.month == 2) & (df.index.day == 29))]
    idx = df.index
    is_leap = idx.is_leap_year.astype(int)
    after_feb_28 = ((idx.month > 2) | ((idx.month == 2) & (idx.day > 28))).astype(int)
    hour = (idx.dayofyear - is_leap * after_feb_28 - 1) * 24 + idx.hour
    hourly = df.assign(hour=hour.values).groupby("hour")["temperature"].mean()
    hourly = hourly.reindex(np.arange(8760)).interpolate(limit_direction="both").ffill().bfill()
    return hourly.to_numpy(dtype=float)


def _reshape_temperature_demand(demand: dict[Any, float], periods: list[int], temp: np.ndarray, mode: str, elasticity: float) -> None:
    if not demand:
        return
    baseline_temp = _baseline_temperature_profile()
    indices = _period_indices(periods, len(baseline_temp))
    baseline = baseline_temp[indices]

    if mode == "heat":
        signal = np.maximum(18.0 - temp, 0.0)
        base_signal = np.maximum(18.0 - baseline, 0.0)
    else:
        signal = np.maximum(temp - 22.0, 0.0)
        base_signal = np.maximum(baseline - 22.0, 0.0)

    signal_mean = float(np.mean(signal)) or 1.0
    base_mean = float(np.mean(base_signal)) or signal_mean or 1.0
    shape_multiplier = 1.0 + float(elasticity) * ((signal / signal_mean) - (base_signal / base_mean))
    annual_multiplier = 1.0 + float(elasticity) * ((signal_mean / base_mean) - 1.0)
    multiplier_by_period = {
        int(period): max(0.0, float(shape_multiplier[i] * annual_multiplier))
        for i, period in enumerate(periods)
    }

    for key, value in list(demand.items()):
        if not isinstance(key, tuple) or len(key) < 2:
            continue
        period = int(key[1])
        if period in multiplier_by_period:
            demand[key] = float(value) * multiplier_by_period[period]


def _preserve_total_under_cap(values: np.ndarray, target_total: float, cap: float) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if target_total <= 0.0 or cap <= 0.0:
        return np.zeros_like(values)
    capped = np.minimum(np.maximum(values, 0.0), cap)
    feasible_total = min(float(target_total), cap * len(capped))
    for _ in range(20):
        diff = feasible_total - float(capped.sum())
        if abs(diff) <= max(1e-8, feasible_total * 1e-10):
            break
        if diff > 0.0:
            room = np.maximum(cap - capped, 0.0)
            room_sum = float(room.sum())
            if room_sum <= 1e-12:
                break
            capped = np.minimum(capped + room * min(1.0, diff / room_sum), cap)
        else:
            positive = capped > 0.0
            positive_sum = float(capped[positive].sum())
            if positive_sum <= 1e-12:
                break
            capped[positive] = np.maximum(
                capped[positive] + capped[positive] * (diff / positive_sum), 0.0
            )
    return capped


def _reshape_prepared_temperature_demand(
    demand: dict[Any, float],
    periods: list[int],
    scenario_temp: np.ndarray,
    normal_temp: np.ndarray,
    mode: str,
    strength: float,
    peak_safe: bool,
) -> dict[str, float]:
    if not demand or not periods:
        return {"units_scaled": 0, "total_error": 0.0, "peak_ratio_max": 0.0}

    if mode == "heat":
        scenario_driver = np.maximum(18.0 - scenario_temp, 0.0)
        normal_driver = np.maximum(18.0 - normal_temp, 0.0)
    elif mode == "cool":
        scenario_driver = np.maximum(scenario_temp - 22.0, 0.0)
        normal_driver = np.maximum(normal_temp - 22.0, 0.0)
    else:
        raise ValueError(mode)

    positive_normal = normal_driver[normal_driver > 0.0]
    reference = max(float(np.percentile(positive_normal, 75)), 1.0) if len(positive_normal) else 1.0
    factor = 1.0 + float(strength) * ((scenario_driver - normal_driver) / reference)
    factor = np.clip(factor, 0.75, 1.25)
    factor = pd.Series(factor).rolling(window=24, center=True, min_periods=1).mean().to_numpy(dtype=float)
    factor_by_period = {int(period): float(factor[i]) for i, period in enumerate(periods)}

    keys_by_unit: dict[str, list[tuple[Any, ...]]] = {}
    for key in demand:
        if isinstance(key, tuple) and len(key) >= 2 and int(key[1]) in factor_by_period:
            keys_by_unit.setdefault(str(key[0]), []).append(key)

    total_error = 0.0
    peak_ratios: list[float] = []
    units_scaled = 0
    for keys in keys_by_unit.values():
        keys.sort(key=lambda item: int(item[1]))
        base = np.asarray([float(demand[key]) for key in keys], dtype=float)
        total = float(base.sum())
        if total <= 0.0:
            continue
        multipliers = np.asarray([factor_by_period[int(key[1])] for key in keys], dtype=float)
        scaled = base * multipliers
        scaled_sum = float(scaled.sum())
        if scaled_sum > 0.0:
            scaled *= total / scaled_sum
        base_peak = float(np.max(base))
        if peak_safe and base_peak > 0.0:
            scaled = _preserve_total_under_cap(scaled, total, base_peak)
        for key, value in zip(keys, scaled):
            demand[key] = float(value)
        total_error = max(total_error, abs(float(scaled.sum()) - total))
        if base_peak > 0.0:
            peak_ratios.append(float(np.max(scaled)) / base_peak)
        units_scaled += 1

    return {
        "units_scaled": units_scaled,
        "total_error": total_error,
        "peak_ratio_max": max(peak_ratios) if peak_ratios else 0.0,
    }


def _update_peak_lists(globs: dict[str, Any], demand_name: str, peak_name: str) -> None:
    demand = globs.get(demand_name)
    if not isinstance(demand, dict):
        return
    peaks: dict[str, float] = {}
    for key, value in demand.items():
        if isinstance(key, tuple) and len(key) >= 2:
            unit = str(key[0])
            peaks[unit] = max(peaks.get(unit, 0.0), float(value))
    globs[peak_name] = peaks


def _theoretical_cop(
    temp_c: np.ndarray,
    sink_temp_c: float = 45.0,
    carnot_efficiency: float = 0.36,
    min_cop: float = 1.2,
    max_cop: float = 5.5,
) -> np.ndarray:
    sink_kelvin = float(sink_temp_c) + 273.15
    source_kelvin = np.asarray(temp_c, dtype=float) + 273.15
    temperature_lift = np.maximum(sink_kelvin - source_kelvin, 1.0)
    return np.clip(float(carnot_efficiency) * sink_kelvin / temperature_lift, min_cop, max_cop)


def _temperature_sensitive_cop(
    baseline_values: np.ndarray,
    temp_c: np.ndarray,
    baseline_temp_c: np.ndarray,
    sensitivity: float = 0.85,
) -> np.ndarray:
    reference = _theoretical_cop(baseline_temp_c)
    current = _theoretical_cop(temp_c)
    ratio = np.divide(current, reference, out=np.ones_like(current), where=reference > 0.0)
    updated = np.asarray(baseline_values, dtype=float) * np.power(ratio, float(sensitivity))
    baseline_mean = float(np.nanmean(baseline_values))
    updated_mean = float(np.nanmean(updated))
    if baseline_mean > 0.0 and updated_mean > 0.0:
        updated *= baseline_mean / updated_mean
    return np.clip(updated, 1.2, 5.5)


def _update_prepared_cop(
    globs: dict[str, Any],
    periods: list[int],
    temp_target: np.ndarray,
    country_code: str,
) -> tuple[list[str], float]:
    hp_cop = globs.get("HP_COP_period")
    if not isinstance(hp_cop, dict):
        return [], math.nan

    baseline_profile = _baseline_temperature_profile()
    period_to_position = {int(period): idx for idx, period in enumerate(periods)}
    target_units = {"Air_to_water_HP", "Air_to_water_HP_DH"}
    grouped: dict[tuple[str, str, str], list[tuple]] = {}
    for key in hp_cop:
        if not isinstance(key, tuple) or len(key) < 4:
            continue
        unit, period, zone, system = key[:4]
        if str(unit) not in target_units or str(system).upper() != country_code.upper():
            continue
        if int(period) not in period_to_position:
            continue
        grouped.setdefault((str(unit), str(zone), str(system)), []).append(key)

    updated_units: list[str] = []
    updated_values: list[float] = []
    for (unit, _zone, _system), keys in grouped.items():
        keys.sort(key=lambda key: period_to_position[int(key[1])])
        positions = np.array([period_to_position[int(key[1])] for key in keys], dtype=int)
        baseline_values = np.array([float(hp_cop[key]) for key in keys], dtype=float)
        hour_indices = np.array([(int(key[1]) - 1) % 8760 for key in keys], dtype=int)
        new_values = _temperature_sensitive_cop(
            baseline_values,
            np.asarray(temp_target, dtype=float)[positions],
            baseline_profile[hour_indices],
        )
        for key, value in zip(keys, new_values):
            hp_cop[key] = float(value)
        updated_units.append(unit)
        updated_values.extend(new_values.tolist())

    return sorted(set(updated_units)), (float(np.mean(updated_values)) if updated_values else math.nan)


def _prepared_profile_frame(planning_year: int) -> pd.DataFrame:
    raw_path = os.environ.get("H2RES_CLIMATE_PROFILE_DAT", "").strip()
    if not raw_path:
        raise ValueError("H2RES_CLIMATE_PROFILE_DAT is required for prepared climate inputs")
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    profile = pd.read_csv(path)
    required = {"period", "solar_cf", "wind_cf", "temperature_c"}
    if not required.issubset(profile.columns):
        raise ValueError(f"Prepared climate profile is missing columns: {sorted(required - set(profile.columns))}")
    if "year" in profile.columns:
        available_years = pd.to_numeric(profile["year"], errors="coerce").dropna().astype(int)
        if _env_flag("H2RES_STRICT_CLIMATE_INPUTS") and planning_year not in set(available_years):
            raise ValueError(f"Prepared climate input does not contain requested planning year {planning_year}")
        selected_year = planning_year if planning_year in set(available_years) else int(available_years.min())
        profile = profile.loc[pd.to_numeric(profile["year"], errors="coerce").eq(selected_year)]
    profile = profile.sort_values("period")
    if _env_flag("H2RES_STRICT_CLIMATE_INPUTS"):
        periods = pd.to_numeric(profile["period"], errors="coerce").to_numpy(float)
        values = profile[["solar_cf", "wind_cf", "temperature_c"]].to_numpy(dtype=float)
        if not np.array_equal(periods, np.arange(1, 8761)):
            raise ValueError("Prepared climate input requires unique consecutive periods 1-8760")
        if not np.isfinite(values).all() or ((values[:, :2] < 0) | (values[:, :2] > 1)).any():
            raise ValueError("Prepared climate input has invalid values or capacity factors")
    return profile



def _prepared_profile(planning_year: int) -> np.ndarray:
    profile = _prepared_profile_frame(planning_year)
    return profile[["solar_cf", "wind_cf", "temperature_c"]].to_numpy(dtype=float)


def _prepared_summary() -> dict[str, Any]:
    raw_path = os.environ.get("H2RES_CLIMATE_SUMMARY_DAT", "").strip()
    if not raw_path:
        return {}
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _map_prepared_investment_limits(
    globs: dict[str, Any],
    current_genco: pd.DataFrame,
    country_code: str,
) -> dict[str, float]:
    limits = globs.get("cap_inv_lim_long_lim")
    if not isinstance(limits, dict):
        return {}
    investment_costs = globs.get("techology_cap_costs_years")
    baseline_path = MODEL_DATA_DIR / "genco_data_HR_sdewes18.csv"
    if not baseline_path.exists():
        return {}
    baseline = _read_genco(baseline_path)
    years = [int(year) for year in globs.get("years", [])]
    diagnostics: dict[str, float] = {}

    for technology, label, cost_template in (
        ("WTON", "wind", "HR_Wind"),
        ("PHOT", "solar", "HR_PV"),
    ):
        old_rows = baseline.loc[
            baseline["System"].astype(str).str.upper().eq(country_code.upper())
            & baseline["technology"].astype(str).str.upper().eq(technology),
            ["unit_name", "Zone"],
        ].drop_duplicates()
        new_rows = current_genco.loc[
            current_genco["System"].astype(str).str.upper().eq(country_code.upper())
            & current_genco["technology"].astype(str).str.upper().eq(technology),
            ["unit_name", "cap_mw", "Zone"],
        ].drop_duplicates("unit_name")
        new_rows["cap_mw"] = pd.to_numeric(new_rows["cap_mw"], errors="coerce").fillna(0.0)
        total_capacity = float(new_rows["cap_mw"].sum())
        if old_rows.empty or new_rows.empty or total_capacity <= 0.0:
            continue

        mapped_total = 0.0
        for year in years:
            original_total = sum(
                float(limits.get((str(row.unit_name), int(year), str(row.Zone)), 0.0))
                for row in old_rows.itertuples(index=False)
            )
            for row in new_rows.itertuples(index=False):
                value = original_total * float(row.cap_mw) / total_capacity
                limits[(str(row.unit_name), int(year), str(row.Zone))] = value
                if isinstance(investment_costs, dict):
                    template_key = (cost_template, int(year))
                    if template_key in investment_costs:
                        investment_costs[(str(row.unit_name), int(year))] = float(investment_costs[template_key])
                mapped_total += value
        diagnostics[f"{label}_investment_limit_total"] = mapped_total
    return diagnostics


def apply_climate_inputs_to_model_globals(globs: dict[str, Any]) -> ClimateApplyResult | None:
    if not _env_flag("H2RES_CLIMATE_ENABLED", "1"):
        return None

    scenario_name = os.environ.get("H2RES_CLIMATE_SCENARIO", "avg_yield__normal")
    country_code = os.environ.get("H2RES_CLIMATE_COUNTRY", "HR").upper()
    planning_year = int(os.environ.get("H2RES_CLIMATE_PLANNING_YEAR", str(min(globs.get("years", [2020])))))

    if _env_flag("H2RES_CLIMATE_PREPARED_INPUTS", "0"):
        profile_frame = _prepared_profile_frame(planning_year)
        profile = profile_frame[["solar_cf", "wind_cf", "temperature_c"]].to_numpy(dtype=float)
        periods = [int(p) for p in globs.get("periods", [])]
        if not periods:
            return None
        years = [int(y) for y in globs.get("years", [planning_year])]
        indices = _period_indices(periods, profile.shape[0])
        temp_target = profile[indices, 2]
        normal_temperature = (
            pd.to_numeric(profile_frame["normal_temperature_c"], errors="coerce")
            .interpolate(limit_direction="both")
            .ffill()
            .bfill()
            .to_numpy(dtype=float)
            if "normal_temperature_c" in profile_frame
            else profile[:, 2]
        )
        normal_temp_target = normal_temperature[indices]
        availability = globs.get("aval_factor_plant")
        genco = _read_genco(globs.get("genco_dat", MODEL_DATA_DIR / "genco_data_HR_sdewes18.csv"))
        solar_units, _solar_caps = _select_units(genco, availability, country_code, "PHOT")
        wind_units, _wind_caps = _select_units(genco, availability, country_code, "WTON")
        investment_diagnostics = _map_prepared_investment_limits(globs, genco, country_code)
        cop_units, cop_mean = _update_prepared_cop(globs, periods, temp_target, country_code)
        summary = _prepared_summary()
        temperature_demand_diagnostics: dict[str, dict[str, float]] = {}
        if bool(summary.get("temperature_demand_runtime_scaling", False)):
            strength_scale = float(summary.get("temperature_demand_strength_scale", 1.0))
            peak_safe = bool(summary.get("temperature_demand_peak_safe", False))
            for demand_name, mode, strength in (
                ("heat_demand_indv", "heat", 0.35),
                ("heat_demand_DH", "heat", 0.35),
                ("cool_demand_indv", "cool", 0.25),
                ("cool_demand_DH", "cool", 0.25),
            ):
                from scripts.temperature_scope import reshape_demand_for_zones
                target_zones = set(genco.loc[
                    genco["System"].astype(str).str.upper().eq(country_code.upper()), "Zone"
                ].astype(str))
                temperature_demand_diagnostics[demand_name] = reshape_demand_for_zones(
                    globs.get(demand_name, {}), target_zones,
                    lambda subset: _reshape_prepared_temperature_demand(
                        subset, periods, temp_target, normal_temp_target,
                        mode, strength * strength_scale, peak_safe,
                    ),
                )
            _update_peak_lists(globs, "heat_demand_indv", "heat_demand_long_max_values_list")
            _update_peak_lists(globs, "heat_demand_DH", "heat_demand_DH_long_max_values_list")
            _update_peak_lists(globs, "cool_demand_indv", "cool_demand_long_max_values_list")
        globs["temperature_demand_runtime_diagnostics"] = temperature_demand_diagnostics
        result = ClimateApplyResult(
            scenario=scenario_name,
            probability=float(summary.get("probability", 0.0)),
            cluster_size=int(summary.get("cluster_size", 0)),
            solar_units=solar_units,
            wind_units=wind_units,
            solar_mass_error=float(summary.get("solar_mass_mean_abs_error", math.nan)),
            wind_mass_error=float(summary.get("wind_mass_mean_abs_error", math.nan)),
            temperature_mean_c=float(np.mean(temp_target)),
            cop_units=cop_units,
            cop_mean=cop_mean,
            prepared_inputs=True,
        )
        globs["last_climate_apply_result"] = result
        print(
            "[CLIMATE] prepared spatial inputs: "
            f"scenario={result.scenario}, solar_units={len(result.solar_units)}, "
            f"wind_units={len(result.wind_units)}, cop_units={len(result.cop_units)}, "
            f"wind_mass_err={result.wind_mass_error:.6f}, temp_mean={result.temperature_mean_c:.2f}C, "
            f"investment_limits_mapped={bool(investment_diagnostics)}"
        )
        return result

    scenarios = get_representative_scenarios(planning_year)
    if scenario_name not in scenarios:
        scenario_name = "avg_yield__normal"
    scenario = scenarios[scenario_name]
    profile = np.asarray(scenario["profile"], dtype=float)

    periods = [int(p) for p in globs.get("periods", [])]
    if not periods:
        return None
    years = [int(y) for y in globs.get("years", [planning_year])]
    indices = _period_indices(periods, profile.shape[0])
    solar_target = profile[indices, 0]
    wind_target = profile[indices, 1]
    temp_target = profile[indices, 2]

    availability = globs.get("aval_factor_plant")
    if not isinstance(availability, dict):
        return None
    genco = _read_genco(globs.get("genco_dat", MODEL_DATA_DIR / "genco_data_HR_sdewes18.csv"))

    solar_units, solar_caps = _select_units(genco, availability, country_code, "PHOT")
    wind_units, wind_caps = _select_units(genco, availability, country_code, "WTON")

    solar_err = _scale_unit_profiles(availability, solar_units, solar_caps, periods, years, solar_target)
    wind_err = _scale_unit_profiles(availability, wind_units, wind_caps, periods, years, wind_target)

    _reshape_temperature_demand(globs.get("heat_demand_indv", {}), periods, temp_target, "heat", 0.70)
    _reshape_temperature_demand(globs.get("heat_demand_DH", {}), periods, temp_target, "heat", 0.90)
    _reshape_temperature_demand(globs.get("cool_demand_indv", {}), periods, temp_target, "cool", 0.95)
    _reshape_temperature_demand(globs.get("cool_demand_DH", {}), periods, temp_target, "cool", 0.95)
    _update_peak_lists(globs, "heat_demand_indv", "heat_demand_long_max_values_list")
    _update_peak_lists(globs, "heat_demand_DH", "heat_demand_DH_long_max_values_list")
    _update_peak_lists(globs, "cool_demand_indv", "cool_demand_long_max_values_list")

    result = ClimateApplyResult(
        scenario=scenario_name,
        probability=float(scenario["probability"]),
        cluster_size=int(scenario["cluster_size"]),
        solar_units=solar_units,
        wind_units=wind_units,
        solar_mass_error=solar_err,
        wind_mass_error=wind_err,
        temperature_mean_c=float(np.mean(temp_target)),
        cop_units=[],
        cop_mean=math.nan,
        prepared_inputs=False,
    )
    globs["last_climate_apply_result"] = result
    print(
        "[CLIMATE] "
        f"scenario={result.scenario}, prob={result.probability:.1%}, "
        f"solar_units={len(result.solar_units)}, wind_units={len(result.wind_units)}, "
        f"solar_mass_err={result.solar_mass_error:.6f}, wind_mass_err={result.wind_mass_error:.6f}, "
        f"temp_mean={result.temperature_mean_c:.2f}C"
    )
    return result


def quick_sensitivity(planning_year: int = 2020) -> pd.DataFrame:
    scenarios = get_representative_scenarios(planning_year)
    genco = _read_genco(MODEL_DATA_DIR / "genco_data_HR_sdewes18.csv")
    avail = pd.read_csv(MODEL_DATA_DIR / "ncre_aval_factor_HR_2020_2050_sdewes7.csv")
    availability_units = set(avail.columns) - {"period"}

    def capacity_sum(tech: str) -> float:
        units, caps = _select_units(genco, {(u, 1): 0.0 for u in availability_units}, "HR", tech)
        return float(sum(caps.get(u, 0.0) for u in units))

    solar_cap = capacity_sum("PHOT")
    wind_cap = capacity_sum("WTON")
    rows = []
    for name in DIAGONAL_SCENARIOS:
        profile = scenarios[name]["profile"]
        solar_cf = float(np.mean(profile[:, 0]))
        wind_cf = float(np.mean(profile[:, 1]))
        temp = profile[:, 2]
        rows.append(
            {
                "scenario": name,
                "probability": float(scenarios[name]["probability"]),
                "cluster_size": int(scenarios[name]["cluster_size"]),
                "solar_cf_mean": solar_cf,
                "wind_cf_mean": wind_cf,
                "temperature_mean_c": float(np.mean(temp)),
                "heating_degree_hours_18c": float(np.maximum(18.0 - temp, 0.0).sum()),
                "cooling_degree_hours_22c": float(np.maximum(temp - 22.0, 0.0).sum()),
                "solar_proxy_gwh": solar_cf * solar_cap * 8760.0 / 1000.0,
                "wind_proxy_gwh": wind_cf * wind_cap * 8760.0 / 1000.0,
                "solar_capacity_mw": solar_cap,
                "wind_capacity_mw": wind_cap,
            }
        )
    return pd.DataFrame(rows)


def validate_runtime_inputs() -> dict[str, Any]:
    required = [
        DEFAULT_SCENARIO_FILE,
        MODEL_DATA_DIR / "genco_data_HR_sdewes18.csv",
        MODEL_DATA_DIR / "ncre_aval_factor_HR_2020_2050_sdewes7.csv",
    ]
    optional_sources = [
        CLIMATE_DATA_DIR / "HR_data.parquet",
        CLIMATE_DATA_DIR / "HR_wind_nuts_profiles.parquet",
        CLIMATE_DATA_DIR / "HR_solar_macro_profiles.parquet",
    ]
    missing = [str(path.relative_to(PROJECT_ROOT)) for path in required if not path.exists()]
    optional_available = [str(path.relative_to(PROJECT_ROOT)) for path in optional_sources if path.exists()]
    optional_missing = [str(path.relative_to(PROJECT_ROOT)) for path in optional_sources if not path.exists()]
    scenarios = get_representative_scenarios(2020)
    quick = quick_sensitivity(2020)
    return {
        "ok": not missing,
        "missing": missing,
        "optional_sources_available": optional_available,
        "optional_sources_missing": optional_missing,
        "scenarios": {
            name: {
                "probability": float(scenarios[name]["probability"]),
                "cluster_size": int(scenarios[name]["cluster_size"]),
            }
            for name in DIAGONAL_SCENARIOS
        },
        "quick_sensitivity": quick.to_dict(orient="records"),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

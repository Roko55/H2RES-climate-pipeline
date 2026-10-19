# -*- coding: utf-8 -*-
"""
Scenario Manager — Climate Scenario Selection and Decomposition for H2RES.

This module bridges the XGBoost climate scenario generator and the H2RES
energy-system optimisation model.  It performs three tasks:

1. **Loading** — reads an ensemble of synthetic climate years produced by
   ``xgboost_climate_model.py`` (shape ``(N, 8760, 3)``).
2. **Clustering** — partitions the ensemble into three representative
   scenarios (Low / Average / High yield) using K-Means on cumulative
   renewable-energy potential, then selects the medoid of each cluster.
3. **Decomposition** — maps the single-channel aggregate solar and wind
   profiles back to individual H2RES generation units using historical
   capacity-weighted ratio profiles from the ``ncre_aval_factor`` CSV.

The decomposition preserves inter-unit diversity (e.g., different wind-farm
locations have distinct capacity-factor profiles) while inheriting the
stochastic variability from the generated scenarios.
"""

import os
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from typing import Optional


# =============================================================================
# CONFIGURATION
# =============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data", "SC")

NUM_SYNTHETIC_YEARS = 100
NUM_CLUSTERS = 3  # Low, Average, High yield scenarios

# Channel indices in the (8760, 3) scenario array
SOLAR_IDX = 0
WIND_IDX = 1
TEMP_IDX = 2

# H2RES unit capacities (MW).
# Solar and wind solver units are loaded from genco_data; wind schema files are
# kept only as backward-compatible fallbacks.
HROR_UNIT = "hror"
AUTO_NUTS3_MAX_ACTIVE = 6
AUTO_NUTS3_MAX_SITES = 60


def _load_solar_units_from_genco(country_code: str = "HR") -> dict:
    """
    Auto-detect solar unit capacities from the genco_data CSV (technology=PHOT).

    Returns dict {unit_name: cap_mw}.
    """
    genco_path = os.path.join(DATA_DIR, f"genco_data_{country_code}_sdewes.csv")
    if not os.path.exists(genco_path):
        print(f"  [WARN] No genco_data found for {country_code}, using fallback SOLAR_UNITS")
        return {"HR_Solar_HR_COAST_SOUTH": 257.0, "HR_Solar_HR_EAST": 159.5, "HR_Solar_HR_NORTH_CENTRAL": 46.0}

    df = pd.read_csv(genco_path)
    solar = df[df["technology"] == "PHOT"][["unit_name", "cap_mw"]].copy()
    units = dict(zip(solar["unit_name"], solar["cap_mw"].astype(float)))

    if not units:
        print(f"  [WARN] No PHOT units found in {genco_path}, using fallback SOLAR_UNITS")
        return {"HR_Solar_HR_COAST_SOUTH": 257.0, "HR_Solar_HR_EAST": 159.5, "HR_Solar_HR_NORTH_CENTRAL": 46.0}

    print(f"  [SOLAR_UNITS] Loaded from genco_data: {genco_path}")
    for name, cap in sorted(units.items()):
        print(f"    {name}: {cap:.1f} MW")
    return units


def _load_wind_units(country_code: str = "HR") -> dict:
    """
    Auto-detect wind unit capacities from the active H2RES input data.

    Priority:
    1. genco_data_{CC}_sdewes.csv (actual solver units)
    2. wind_schema_{CC}_auto.csv  (legacy generated fallback)
    3. wind_schema_{CC}.csv       (legacy manual fallback)

    Returns dict {unit_name: total_cap_mw}.
    """
    genco_path = os.path.join(DATA_DIR, f"genco_data_{country_code}_sdewes.csv")
    if os.path.exists(genco_path):
        df = pd.read_csv(genco_path)
        wind = df[df["fuel_type"] == "Wind"][["unit_name", "cap_mw"]].copy()
        wind["cap_mw"] = pd.to_numeric(wind["cap_mw"], errors="coerce").fillna(0.0)
        units = wind.groupby("unit_name")["cap_mw"].sum().to_dict()

        if units:
            print(f"  [WIND_UNITS] Loaded from genco_data: {genco_path}")
            for name, cap in sorted(units.items()):
                print(f"    {name}: {cap:.1f} MW")
            return units

    auto_schema = os.path.join(DATA_DIR, f"wind_schema_{country_code}_auto.csv")
    manual_schema = os.path.join(DATA_DIR, f"wind_schema_{country_code}.csv")

    schema_path = None
    if os.path.exists(auto_schema):
        schema_path = auto_schema
    elif os.path.exists(manual_schema):
        schema_path = manual_schema

    if schema_path is None:
        print(f"  [WARN] No wind schema found for {country_code}, using empty WIND_UNITS")
        return {}

    df = pd.read_csv(schema_path)
    df["cap_mw_old"] = pd.to_numeric(df["cap_mw_old"], errors="coerce")
    units = df.groupby("new_unit_name")["cap_mw_old"].sum().to_dict()

    src = "auto-schema" if "auto" in os.path.basename(schema_path) else "manual-schema"
    print(f"  [WIND_UNITS] Loaded from {src}: {schema_path}")
    for name, cap in sorted(units.items()):
        print(f"    {name}: {cap:.1f} MW")

    return units


def _normalize_country_code(country_code: str) -> str:
    return str(country_code or "HR").upper()


_solar_units_cache = {}
_wind_units_cache = {}


def _get_solar_units(country_code: str = "HR") -> dict:
    cc = _normalize_country_code(country_code)
    if cc not in _solar_units_cache:
        _solar_units_cache[cc] = _load_solar_units_from_genco(cc)
    return _solar_units_cache[cc]


def _get_wind_units(country_code: str = "HR") -> dict:
    cc = _normalize_country_code(country_code)
    if cc not in _wind_units_cache:
        _wind_units_cache[cc] = _load_wind_units(cc)
    return _wind_units_cache[cc]

# Climate change scaling (linear interpolation from base_year to target_year)
# These defaults are overridden by modeling_approaches.py at runtime.
CLIMATE_CHANGE = {
    "base_year": 2020,
    "target_year": 2050,
    "solar_factor_2050": 1.03,   # +3% solar irradiance by 2050
    "wind_factor_2050": 0.92,    # -8% wind speed by 2050
    "temp_offset_2050": 1.5,     # +1.5°C temperature increase by 2050
}

# Cached ratio profiles (computed once per country/resolution, reused across calls)
_ratio_cache = {}


# =============================================================================
# SCENARIO LOADING & CLUSTERING
# =============================================================================
def _cluster_axis(values: np.ndarray,
                  ordered_labels: list[str],
                  random_state: int = 42) -> tuple[np.ndarray, dict]:
    """Cluster a scalar metric into ordered labels and return medoid metadata."""
    kmeans = KMeans(n_clusters=len(ordered_labels), random_state=random_state, n_init=10)
    raw_labels = kmeans.fit_predict(values.reshape(-1, 1))
    raw_centers = kmeans.cluster_centers_.reshape(-1)
    order = np.argsort(raw_centers)

    mapped_labels = np.empty(values.shape[0], dtype=object)
    info = {}
    for rank, cluster_id in enumerate(order):
        label = ordered_labels[rank]
        mask = raw_labels == cluster_id
        indices = np.where(mask)[0]
        cluster_values = values[mask]
        center = float(cluster_values.mean()) if len(cluster_values) else float(raw_centers[cluster_id])
        if len(indices):
            medoid_local = int(np.argmin(np.abs(cluster_values - center)))
            medoid_idx = int(indices[medoid_local])
        else:
            medoid_idx = int(np.argmin(np.abs(values - center)))
        mapped_labels[mask] = label
        info[label] = {
            "cluster_id": int(cluster_id),
            "center": center,
            "size": int(len(indices)),
            "medoid_idx": medoid_idx,
        }
    return mapped_labels, info


def _temperature_stress_metrics(temp_profiles: np.ndarray,
                                heating_base_c: float = 18.0,
                                cooling_base_c: float = 22.0) -> dict:
    """Compute thermal-stress metrics used for scenario design."""
    hdd = np.maximum(float(heating_base_c) - temp_profiles, 0.0).sum(axis=1)
    cdd = np.maximum(temp_profiles - float(cooling_base_c), 0.0).sum(axis=1)
    temp_mean = temp_profiles.mean(axis=1)

    hdd_std = float(np.std(hdd))
    cdd_std = float(np.std(cdd))
    hdd_z = (hdd - float(np.mean(hdd))) / (hdd_std if hdd_std > 0 else 1.0)
    cdd_z = (cdd - float(np.mean(cdd))) / (cdd_std if cdd_std > 0 else 1.0)
    thermal_index = cdd_z - hdd_z

    return {
        "hdd_sum": hdd,
        "cdd_sum": cdd,
        "temp_mean": temp_mean,
        "thermal_index": thermal_index,
    }


def _build_representative_record(scenarios: np.ndarray,
                                 idx: int,
                                 probability: float,
                                 cluster_size: int,
                                 energy_potentials: np.ndarray,
                                 temp_metrics: dict,
                                 year: int = None) -> dict:
    profile = scenarios[int(idx)].copy()
    if year is not None:
        profile = _apply_climate_scaling(profile, year)
    return {
        "profile": profile,
        "probability": float(probability),
        "cluster_size": int(cluster_size),
        "total_energy": float(energy_potentials[int(idx)]),
        "thermal_index": float(temp_metrics["thermal_index"][int(idx)]),
        "temperature_mean": float(temp_metrics["temp_mean"][int(idx)]),
        "hdd_sum": float(temp_metrics["hdd_sum"][int(idx)]),
        "cdd_sum": float(temp_metrics["cdd_sum"][int(idx)]),
        "representative_idx": int(idx),
    }


def get_representative_scenarios(year: int = None) -> dict:
    """
    Build representative climate scenarios on two axes:

    - renewable yield: ``low_yield`` / ``avg_yield`` / ``high_yield``
    - thermal stress: ``cold`` / ``normal`` / ``hot``

    Legacy one-axis yield scenarios remain available for backward compatibility,
    while combined scenarios use keys such as ``avg_yield__normal``.
    """
    print(f"\nStep 1: Loading {NUM_SYNTHETIC_YEARS} synthetic years...")
    xgb_path = os.path.join(DATA_DIR, "xgb_synthetic_scenarios.npy")

    if os.path.exists(xgb_path):
        print(f"  Loading from: {xgb_path}")
        scenarios = np.load(xgb_path)
    else:
        print("  No pre-generated file found. Running XGBoost generation...")
        try:
            from scripts.xgboost_climate_model import main as xgb_main
        except ImportError:
            from xgboost_climate_model import main as xgb_main
        xgb_main(["--generate", "--num-scenarios", str(NUM_SYNTHETIC_YEARS)])
        scenarios = np.load(xgb_path)

    n_samples = scenarios.shape[0]
    if n_samples != NUM_SYNTHETIC_YEARS:
        print(f"  [NOTE] Working with {n_samples} samples (target was {NUM_SYNTHETIC_YEARS}).")

    print(f"Step 2: Calculating renewable and thermal metrics for {n_samples} samples...")
    solar_sums = scenarios[:, :, SOLAR_IDX].sum(axis=1)
    wind_sums = scenarios[:, :, WIND_IDX].sum(axis=1)
    energy_potentials = solar_sums + wind_sums
    temp_metrics = _temperature_stress_metrics(scenarios[:, :, TEMP_IDX])

    print(f"  Energy range: [{energy_potentials.min():.1f}, {energy_potentials.max():.1f}]")
    print(f"  Mean energy: {energy_potentials.mean():.1f}")
    print(f"  Thermal index range: [{temp_metrics['thermal_index'].min():.3f}, {temp_metrics['thermal_index'].max():.3f}]")
    print(f"  Mean temperature range: [{temp_metrics['temp_mean'].min():.3f}, {temp_metrics['temp_mean'].max():.3f}] C")

    print("Step 3: Clustering renewable yield and thermal stress axes...")
    yield_labels, yield_info = _cluster_axis(
        energy_potentials,
        ["low_yield", "avg_yield", "high_yield"],
        random_state=42,
    )
    thermal_labels, thermal_info = _cluster_axis(
        temp_metrics["thermal_index"],
        ["cold", "normal", "hot"],
        random_state=84,
    )

    print("Step 4: Identifying representative scenarios...")
    result = {}
    for label, info in yield_info.items():
        rec = _build_representative_record(
            scenarios,
            idx=info["medoid_idx"],
            probability=info["size"] / n_samples,
            cluster_size=info["size"],
            energy_potentials=energy_potentials,
            temp_metrics=temp_metrics,
            year=year,
        )
        result[label] = rec
        print(f"  {label}: energy={rec['total_energy']:.1f}, prob={rec['probability']:.1%}, samples={rec['cluster_size']}")

    for label, info in thermal_info.items():
        rec = _build_representative_record(
            scenarios,
            idx=info["medoid_idx"],
            probability=info["size"] / n_samples,
            cluster_size=info["size"],
            energy_potentials=energy_potentials,
            temp_metrics=temp_metrics,
            year=year,
        )
        result[label] = rec
        print(f"  {label}: thermal_index={rec['thermal_index']:.3f}, prob={rec['probability']:.1%}, samples={rec['cluster_size']}")

    combined_method = os.environ.get("H2RES_COMBINED_SCENARIO_METHOD", "joint_medoid").strip().lower()
    if combined_method in {"orthogonal", "fixed_temperature", "orthogonal_axes"}:
        combined_method = "orthogonal"
    else:
        combined_method = "joint_medoid"

    print(f"Step 5: Building combined yield + thermal scenarios ({combined_method})...")
    energy_scale = float(np.std(energy_potentials))
    thermal_scale = float(np.std(temp_metrics["thermal_index"]))
    energy_scale = energy_scale if energy_scale > 0 else 1.0
    thermal_scale = thermal_scale if thermal_scale > 0 else 1.0

    for yield_label, y_info in yield_info.items():
        for thermal_label, t_info in thermal_info.items():
            key = f"{yield_label}__{thermal_label}"
            mask = (yield_labels == yield_label) & (thermal_labels == thermal_label)
            indices = np.where(mask)[0]

            if combined_method == "orthogonal":
                yield_rec = result[yield_label]
                thermal_rec = result[thermal_label]
                profile = yield_rec["profile"].copy()
                profile[:, TEMP_IDX] = thermal_rec["profile"][:, TEMP_IDX]

                rec = {
                    "profile": profile,
                    "probability": float(yield_rec["probability"] * thermal_rec["probability"]),
                    "cluster_size": int(len(indices)),
                    "total_energy": float(yield_rec["total_energy"]),
                    "thermal_index": float(thermal_rec["thermal_index"]),
                    "temperature_mean": float(thermal_rec["temperature_mean"]),
                    "hdd_sum": float(thermal_rec["hdd_sum"]),
                    "cdd_sum": float(thermal_rec["cdd_sum"]),
                    "representative_idx": int(yield_rec["representative_idx"]),
                    "yield_representative_idx": int(yield_rec["representative_idx"]),
                    "thermal_representative_idx": int(thermal_rec["representative_idx"]),
                    "joint_cluster_size": int(len(indices)),
                    "orthogonal_axes": True,
                }
            else:
                target_y = float(y_info["center"])
                target_t = float(t_info["center"])

                if len(indices):
                    d_energy = (energy_potentials[indices] - target_y) / energy_scale
                    d_thermal = (temp_metrics["thermal_index"][indices] - target_t) / thermal_scale
                    medoid_idx = int(indices[int(np.argmin(d_energy ** 2 + d_thermal ** 2))])
                else:
                    d_energy = (energy_potentials - target_y) / energy_scale
                    d_thermal = (temp_metrics["thermal_index"] - target_t) / thermal_scale
                    medoid_idx = int(np.argmin(d_energy ** 2 + d_thermal ** 2))

                rec = _build_representative_record(
                    scenarios,
                    idx=medoid_idx,
                    probability=len(indices) / n_samples,
                    cluster_size=len(indices),
                    energy_potentials=energy_potentials,
                    temp_metrics=temp_metrics,
                    year=year,
                )
                rec["fallback_used"] = bool(len(indices) == 0)
                rec["orthogonal_axes"] = False
            result[key] = rec

    result["metadata"] = {
        "n_samples": int(n_samples),
        "yield_centers": {k: float(v["center"]) for k, v in yield_info.items()},
        "thermal_centers": {k: float(v["center"]) for k, v in thermal_info.items()},
        "yield_axis_labels": ["low_yield", "avg_yield", "high_yield"],
        "thermal_axis_labels": ["cold", "normal", "hot"],
        "default_combined_scenario": "avg_yield__normal",
        "combined_scenario_method": combined_method,
    }

    for key in ["low_yield__cold", "avg_yield__normal", "high_yield__hot"]:
        rec = result[key]
        suffix = " [fallback]" if rec.get("fallback_used") else ""
        sample_label = "joint_samples" if rec.get("orthogonal_axes") else "samples"
        sample_value = rec.get("joint_cluster_size", rec.get("cluster_size", 0))
        print(
            f"  {key}: energy={rec['total_energy']:.1f}, temp_mean={rec['temperature_mean']:.3f}C, "
            f"prob={rec['probability']:.1%}, {sample_label}={sample_value}{suffix}"
        )

    return result


# =============================================================================
# CLIMATE CHANGE SCALING
# =============================================================================
def _apply_climate_scaling(profile: np.ndarray, year: int) -> np.ndarray:
    """
    Apply linear climate-change scaling to a scenario profile.

    The scaling interpolates linearly between ``base_year`` and
    ``target_year`` (defined in the ``CLIMATE_CHANGE`` dict):

    - **Solar**: multiplicative factor on capacity factor.
    - **Wind**: multiplicative factor on capacity factor.
    - **Temperature**: additive offset (°C).

    Parameters are set in ``modeling_approaches.py`` at runtime and passed
    to this module via the ``CLIMATE_CHANGE`` dict.
    """
    base = CLIMATE_CHANGE["base_year"]
    target = CLIMATE_CHANGE["target_year"]

    if year <= base:
        return profile

    # Linear interpolation factor (0 at base_year, 1 at target_year)
    t = min((year - base) / (target - base), 1.0)

    scaled = profile.copy()
    solar_factor = 1.0 + t * (CLIMATE_CHANGE["solar_factor_2050"] - 1.0)
    wind_factor = 1.0 + t * (CLIMATE_CHANGE["wind_factor_2050"] - 1.0)
    temp_offset = t * CLIMATE_CHANGE.get("temp_offset_2050", 0.0)

    scaled[:, SOLAR_IDX] = np.clip(profile[:, SOLAR_IDX] * solar_factor, 0, 1)
    scaled[:, WIND_IDX] = np.clip(profile[:, WIND_IDX] * wind_factor, 0, 1)
    scaled[:, TEMP_IDX] = profile[:, TEMP_IDX] + temp_offset

    if solar_factor != 1.0 or wind_factor != 1.0 or temp_offset != 0.0:
        print(f"  [CLIMATE] Year {year}: solar x{solar_factor:.3f}, "
              f"wind x{wind_factor:.3f}, temp +{temp_offset:.2f}°C")

    return scaled


# =============================================================================
# UNIT DECOMPOSITION: Aggregate → Per-Unit H2RES Profiles
# =============================================================================
def _wind_artifact_paths(country_code: str) -> dict:
    cc = _normalize_country_code(country_code)
    return {
        "sites_meta": os.path.join(DATA_DIR, f"{cc}_wind_sites_meta.csv"),
        "nuts_profiles": os.path.join(DATA_DIR, f"{cc}_wind_nuts_profiles.parquet"),
        "macro_profiles": os.path.join(DATA_DIR, f"{cc}_wind_macro_profiles.parquet"),
        "transfer_nuts": os.path.join(DATA_DIR, f"{cc}_wind_transfer_nuts_to_units.csv"),
        "transfer_macro": os.path.join(DATA_DIR, f"{cc}_wind_transfer_macro_to_units.csv"),
    }


def _get_wind_source_stats(country_code: str) -> dict:
    paths = _wind_artifact_paths(country_code)
    out = {"wind_site_count": 0, "active_wind_nuts3_count": 0}
    if not os.path.exists(paths["sites_meta"]):
        return out
    df = pd.read_csv(paths["sites_meta"])
    out["wind_site_count"] = int(len(df))
    if "nuts_id" in df.columns:
        active = (
            df["nuts_id"].fillna("").astype(str).str.strip()
            .replace("", np.nan).dropna().nunique()
        )
        out["active_wind_nuts3_count"] = int(active)
    return out


def _decide_auto_wind_resolution(active_wind_nuts3_count: int, wind_site_count: int) -> str:
    if active_wind_nuts3_count <= AUTO_NUTS3_MAX_ACTIVE and wind_site_count <= AUTO_NUTS3_MAX_SITES:
        return "nuts3"
    return "macro"


def _resolve_wind_resolution(country_code: str, wind_resolution_mode: str = "auto") -> tuple:
    cc = _normalize_country_code(country_code)
    mode = str(wind_resolution_mode or "auto").lower()
    if mode not in {"auto", "nuts3", "macro"}:
        raise ValueError(f"Invalid wind_resolution_mode='{wind_resolution_mode}'. Use auto|nuts3|macro.")

    stats = _get_wind_source_stats(cc)
    paths = _wind_artifact_paths(cc)
    nuts_ready = os.path.exists(paths["nuts_profiles"]) and os.path.exists(paths["transfer_nuts"])
    macro_ready = os.path.exists(paths["macro_profiles"]) and os.path.exists(paths["transfer_macro"])

    if mode == "macro":
        if not macro_ready:
            raise FileNotFoundError(
                f"Missing macro wind artifacts for {cc}: {paths['macro_profiles']} and/or {paths['transfer_macro']}"
            )
        return "macro", stats

    if mode == "nuts3":
        if nuts_ready:
            return "nuts3", stats
        if macro_ready:
            print(f"  [WARN] wind_resolution_mode='nuts3' requested but NUTS3 artifacts missing for {cc}. Falling back to macro.")
            return "macro", stats
        raise FileNotFoundError(
            f"Missing NUTS3 and macro wind artifacts for {cc}. Run fetch_ninja_data.py first."
        )

    auto_mode = _decide_auto_wind_resolution(
        active_wind_nuts3_count=stats["active_wind_nuts3_count"],
        wind_site_count=stats["wind_site_count"],
    )
    if auto_mode == "nuts3" and nuts_ready:
        return "nuts3", stats
    if macro_ready:
        if auto_mode == "nuts3":
            print(f"  [WARN] AUTO selected NUTS3 for {cc}, but NUTS3 artifacts missing. Falling back to macro.")
        return "macro", stats
    if nuts_ready:
        print(f"  [WARN] AUTO selected macro for {cc}, but macro artifacts missing. Falling back to NUTS3.")
        return "nuts3", stats

    raise FileNotFoundError(f"No usable wind artifacts found for {cc}.")


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


def _load_transfer_matrix(path: str, wind_units: dict) -> tuple:
    tf = pd.read_csv(path)
    required = {"unit_name", "source_zone", "weight"}
    if not required.issubset(tf.columns):
        raise ValueError(f"Transfer matrix missing columns {required}: {path}")

    tf["unit_name"] = tf["unit_name"].astype(str).str.strip()
    tf["source_zone"] = tf["source_zone"].astype(str).str.strip()
    tf["weight"] = pd.to_numeric(tf["weight"], errors="coerce").fillna(0.0).clip(lower=0.0)
    tf = tf[(tf["unit_name"] != "") & (tf["source_zone"] != "")]
    if tf.empty:
        raise ValueError(f"Transfer matrix is empty: {path}")

    pivot = tf.pivot_table(
        index="unit_name", columns="source_zone", values="weight", aggfunc="sum", fill_value=0.0
    ).astype(float)
    unit_list = list(wind_units.keys())
    pivot = pivot.reindex(unit_list, fill_value=0.0)

    row_sums = pivot.sum(axis=1)
    valid_rows = row_sums > 0
    if valid_rows.any():
        pivot.loc[valid_rows] = pivot.loc[valid_rows].div(row_sums[valid_rows], axis=0)

    cap_series = pd.Series({u: float(c) for u, c in wind_units.items()}, dtype=float)
    cap_series = cap_series.reindex(pivot.index).fillna(0.0)
    zone_cap = (pivot.mul(cap_series, axis=0)).sum(axis=0)
    total_zone_cap = float(zone_cap.sum())
    if total_zone_cap <= 0:
        zone_cap_share = pd.Series(1.0 / max(len(pivot.columns), 1), index=pivot.columns)
    else:
        zone_cap_share = zone_cap / total_zone_cap

    # Fill units without mapping using zone capacity shares
    invalid_units = row_sums <= 0
    if invalid_units.any():
        for unit_name in pivot.index[invalid_units]:
            pivot.loc[unit_name] = zone_cap_share.reindex(pivot.columns).values

    return pivot, zone_cap_share


def _solar_artifact_paths(country_code: str) -> dict:
    cc = _normalize_country_code(country_code)
    return {
        "macro_profiles": os.path.join(DATA_DIR, f"{cc}_solar_macro_profiles.parquet"),
        "macro_metadata": os.path.join(DATA_DIR, f"{cc}_solar_macro_metadata.csv"),
        "unit_metadata": os.path.join(DATA_DIR, f"{cc}_solar_unit_metadata.csv"),
        "transfer_macro": os.path.join(DATA_DIR, f"{cc}_solar_transfer_macro_to_units.csv"),
    }


def _load_legacy_solar_hror_ratio_profiles(country_code: str, ncre_csv_path: str = None) -> dict:
    cc = _normalize_country_code(country_code)
    cache_key = ("solar_hror_legacy", cc, ncre_csv_path or "")
    if cache_key in _ratio_cache:
        return _ratio_cache[cache_key]

    if ncre_csv_path is None:
        default_cc = os.path.join(DATA_DIR, f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv")
        fallback_hr = os.path.join(DATA_DIR, "ncre_aval_factor_HR_2020_2050_sdewes.csv")
        ncre_csv_path = default_cc if os.path.exists(default_cc) else fallback_hr

    solar_units = _get_solar_units(cc)

    print(f"  [Decomposition] Loading solar/hror ratios from {ncre_csv_path}")
    df = pd.read_csv(ncre_csv_path)
    df["hour_of_year"] = (pd.to_numeric(df["period"], errors="coerce").fillna(1).astype(int) - 1) % 8760
    hourly = df.groupby("hour_of_year").mean(numeric_only=True).reindex(np.arange(8760)).ffill().bfill()

    total_solar_mw = sum(float(v) for v in solar_units.values())
    solar_agg = np.zeros(8760, dtype=float)
    for unit, cap in solar_units.items():
        if unit in hourly.columns and total_solar_mw > 0:
            solar_agg += hourly[unit].values.astype(float) * (float(cap) / total_solar_mw)

    ratios = {}
    for unit, cap in solar_units.items():
        cap_share = float(cap) / total_solar_mw if total_solar_mw > 0 else 0.0
        if unit in hourly.columns:
            unit_profile = hourly[unit].values.astype(float)
        else:
            print(f"  [WARN] Solar unit '{unit}' not found in ncre CSV; using cap-share fallback.")
            unit_profile = np.full(8760, cap_share, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios[unit] = np.where(solar_agg > 1e-6, unit_profile / solar_agg, cap_share)

    if HROR_UNIT in hourly.columns:
        ratios[HROR_UNIT] = hourly[HROR_UNIT].values.astype(float)
    else:
        print(f"  [WARN] HROR unit '{HROR_UNIT}' not found in ncre CSV; using zeros.")
        ratios[HROR_UNIT] = np.zeros(8760, dtype=float)

    _ratio_cache[cache_key] = ratios
    return ratios


def _load_hror_profile(country_code: str, ncre_csv_path: str = None) -> np.ndarray:
    cc = _normalize_country_code(country_code)
    cache_key = ("hror_profile", cc, ncre_csv_path or "")
    if cache_key in _ratio_cache:
        return _ratio_cache[cache_key]

    if ncre_csv_path is None:
        default_cc = os.path.join(DATA_DIR, f"ncre_aval_factor_{cc}_2020_2050_sdewes.csv")
        fallback_hr = os.path.join(DATA_DIR, "ncre_aval_factor_HR_2020_2050_sdewes.csv")
        ncre_csv_path = default_cc if os.path.exists(default_cc) else fallback_hr

    df = pd.read_csv(ncre_csv_path)
    df["hour_of_year"] = (pd.to_numeric(df["period"], errors="coerce").fillna(1).astype(int) - 1) % 8760
    hourly = df.groupby("hour_of_year").mean(numeric_only=True).reindex(np.arange(8760)).ffill().bfill()
    if HROR_UNIT in hourly.columns:
        profile = hourly[HROR_UNIT].values.astype(float)
    else:
        print(f"  [WARN] HROR unit '{HROR_UNIT}' not found in ncre CSV; using zeros.")
        profile = np.zeros(8760, dtype=float)
    _ratio_cache[cache_key] = profile
    return profile


def _load_solar_spatial_components(country_code: str, solar_units: dict) -> Optional[dict]:
    cc = _normalize_country_code(country_code)
    cache_key = ("solar_spatial", cc)
    if cache_key in _ratio_cache:
        return _ratio_cache[cache_key]

    paths = _solar_artifact_paths(cc)
    if not (os.path.exists(paths["macro_profiles"]) and os.path.exists(paths["transfer_macro"])):
        return None

    source_df = pd.read_parquet(paths["macro_profiles"])
    source_hourly = _hourly_mean_8760(source_df)

    unit_zone_weights, zone_cap_share = _load_transfer_matrix(paths["transfer_macro"], solar_units)
    zones = [z for z in unit_zone_weights.columns if z in source_hourly.columns]
    if not zones:
        return None

    unit_zone_weights = unit_zone_weights[zones]
    row_sums = unit_zone_weights.sum(axis=1)
    valid_rows = row_sums > 0
    if valid_rows.any():
        unit_zone_weights.loc[valid_rows] = unit_zone_weights.loc[valid_rows].div(row_sums[valid_rows], axis=0)

    zone_cap_share = zone_cap_share.reindex(zones).fillna(0.0)
    if float(zone_cap_share.sum()) <= 0:
        zone_cap_share[:] = 1.0 / len(zones)
    else:
        zone_cap_share = zone_cap_share / float(zone_cap_share.sum())

    hist_zone_profiles = source_hourly[zones].astype(float).clip(lower=0.0)
    hist_agg = hist_zone_profiles.mul(zone_cap_share, axis=1).sum(axis=1)
    ratio_df = pd.DataFrame(index=hist_zone_profiles.index)
    for zone in zones:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio_df[zone] = np.where(hist_agg.values > 1e-6, hist_zone_profiles[zone].values / hist_agg.values, 1.0)

    out = {
        "zones": zones,
        "unit_zone_weights": unit_zone_weights,
        "zone_cap_share": zone_cap_share,
        "zone_ratio_profiles": ratio_df.reindex(np.arange(8760)).ffill().bfill(),
    }
    _ratio_cache[cache_key] = out
    return out


def _load_wind_spatial_components(country_code: str, resolution: str, wind_units: dict) -> dict:
    cc = _normalize_country_code(country_code)
    res = str(resolution).lower()
    cache_key = ("wind_spatial", cc, res)
    if cache_key in _ratio_cache:
        return _ratio_cache[cache_key]

    paths = _wind_artifact_paths(cc)
    profile_path = paths["nuts_profiles"] if res == "nuts3" else paths["macro_profiles"]
    transfer_path = paths["transfer_nuts"] if res == "nuts3" else paths["transfer_macro"]

    source_df = pd.read_parquet(profile_path)
    source_hourly = _hourly_mean_8760(source_df)

    unit_zone_weights, zone_cap_share = _load_transfer_matrix(transfer_path, wind_units)
    zones = [z for z in unit_zone_weights.columns if z in source_hourly.columns]
    if not zones:
        raise ValueError(
            f"No overlapping zones between profiles ({profile_path}) and transfer matrix ({transfer_path})."
        )

    unit_zone_weights = unit_zone_weights[zones]
    row_sums = unit_zone_weights.sum(axis=1)
    valid_rows = row_sums > 0
    if valid_rows.any():
        unit_zone_weights.loc[valid_rows] = unit_zone_weights.loc[valid_rows].div(row_sums[valid_rows], axis=0)

    zone_cap_share = zone_cap_share.reindex(zones).fillna(0.0)
    if float(zone_cap_share.sum()) <= 0:
        zone_cap_share[:] = 1.0 / len(zones)
    else:
        zone_cap_share = zone_cap_share / float(zone_cap_share.sum())

    hist_zone_profiles = source_hourly[zones].astype(float).clip(lower=0.0)
    hist_agg = hist_zone_profiles.mul(zone_cap_share, axis=1).sum(axis=1)
    ratio_df = pd.DataFrame(index=hist_zone_profiles.index)
    for zone in zones:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio_df[zone] = np.where(hist_agg.values > 1e-6, hist_zone_profiles[zone].values / hist_agg.values, 1.0)

    out = {
        "zones": zones,
        "unit_zone_weights": unit_zone_weights,
        "zone_cap_share": zone_cap_share,
        "zone_ratio_profiles": ratio_df.reindex(np.arange(8760)).ffill().bfill(),
    }
    _ratio_cache[cache_key] = out
    return out


def decompose_to_h2res_units(scenario_profile: np.ndarray,
                              ncre_csv_path: str = None,
                              country_code: str = "HR",
                              wind_resolution_mode: str = "auto",
                              return_diagnostics: bool = False,
                              include_hror: bool = True) -> dict:
    """
    Decompose one aggregate scenario into per-unit H2RES profiles.

    Parameters
    ----------
    scenario_profile : np.ndarray
        Shape ``(8760, 3)`` with channels [Solar, Wind, Temperature].
    ncre_csv_path : str, optional
        Optional override for solar/hror ratio source CSV.
    country_code : str
        ISO2 code used to load country-specific decomposition artifacts.
    wind_resolution_mode : str
        ``auto`` / ``nuts3`` / ``macro`` spatial mode for wind decomposition.
    return_diagnostics : bool
        When True, returns ``(profiles, diagnostics)``.

    Returns
    -------
    dict
        ``{unit_name: (8760,) array}`` for all generation units including
        ``hror``.
    """
    cc = _normalize_country_code(country_code)
    solar_units = _get_solar_units(cc)
    wind_units = _get_wind_units(cc)
    hror_profile = _load_hror_profile(cc, ncre_csv_path=ncre_csv_path) if include_hror else None

    result = {}
    diagnostics = {}

    solar_scenario = scenario_profile[:, SOLAR_IDX]
    solar_components = _load_solar_spatial_components(cc, solar_units)
    if solar_units and solar_components is not None:
        zones = solar_components["zones"]
        ratio_df = solar_components["zone_ratio_profiles"][zones]
        zone_scenarios = ratio_df.mul(solar_scenario, axis=0).clip(0.0, 1.0)
        unit_zone_weights = solar_components["unit_zone_weights"]

        for unit in solar_units:
            if unit in unit_zone_weights.index:
                weights = unit_zone_weights.loc[unit, zones].values.astype(float)
            else:
                weights = solar_components["zone_cap_share"].reindex(zones).values.astype(float)
            unit_profile = zone_scenarios.values @ weights
            result[unit] = np.clip(unit_profile, 0.0, 1.0)

        total_solar_mw = float(sum(float(v) for v in solar_units.values()))
        if total_solar_mw > 0:
            agg_from_units = np.zeros(8760, dtype=float)
            for unit, cap in solar_units.items():
                agg_from_units += result[unit] * (float(cap) / total_solar_mw)
            solar_mean_abs_err = float(np.mean(np.abs(agg_from_units - solar_scenario)))
        else:
            solar_mean_abs_err = 0.0

        diagnostics.update({
            "solar_resolution_selected": "macro",
            "solar_source_zone_count": int(len(zones)),
            "solar_mass_mean_abs_error": solar_mean_abs_err,
        })
        print(
            f"  [SOLAR] country={cc}, resolution=macro, "
            f"zones={diagnostics['solar_source_zone_count']}, "
            f"mass_err={solar_mean_abs_err:.6f}"
        )
    else:
        legacy_ratios = _load_legacy_solar_hror_ratio_profiles(cc, ncre_csv_path=ncre_csv_path)
        for unit in solar_units:
            if unit not in legacy_ratios:
                print(f"  [WARN] Missing solar ratio for '{unit}', using zeros.")
                result[unit] = np.zeros(8760, dtype=float)
                continue
            result[unit] = np.clip(solar_scenario * legacy_ratios[unit], 0.0, 1.0)
        diagnostics.update({
            "solar_resolution_selected": "legacy_ratio",
            "solar_source_zone_count": 0,
            "solar_mass_mean_abs_error": float("nan"),
        })

    wind_scenario = scenario_profile[:, WIND_IDX]
    if wind_units:
        selected_mode, stats = _resolve_wind_resolution(cc, wind_resolution_mode=wind_resolution_mode)
        components = _load_wind_spatial_components(cc, selected_mode, wind_units)
        zones = components["zones"]
        ratio_df = components["zone_ratio_profiles"][zones]
        zone_scenarios = ratio_df.mul(wind_scenario, axis=0).clip(0.0, 1.0)

        unit_zone_weights = components["unit_zone_weights"]
        for unit in wind_units:
            if unit in unit_zone_weights.index:
                weights = unit_zone_weights.loc[unit, zones].values.astype(float)
            else:
                weights = components["zone_cap_share"].reindex(zones).values.astype(float)
            unit_profile = zone_scenarios.values @ weights
            result[unit] = np.clip(unit_profile, 0.0, 1.0)

        total_wind_mw = float(sum(float(v) for v in wind_units.values()))
        if total_wind_mw > 0:
            agg_from_units = np.zeros(8760, dtype=float)
            for unit, cap in wind_units.items():
                agg_from_units += result[unit] * (float(cap) / total_wind_mw)
            mean_abs_err = float(np.mean(np.abs(agg_from_units - wind_scenario)))
        else:
            mean_abs_err = 0.0

        diagnostics.update({
            "country_code": cc,
            "wind_resolution_selected": selected_mode,
            "wind_site_count": stats.get("wind_site_count", 0),
            "active_wind_nuts3_count": stats.get("active_wind_nuts3_count", 0),
            "wind_mass_mean_abs_error": mean_abs_err,
        })
        print(
            f"  [WIND] country={cc}, resolution={selected_mode}, "
            f"sites={diagnostics['wind_site_count']}, "
            f"active_nuts3={diagnostics['active_wind_nuts3_count']}, "
            f"mass_err={mean_abs_err:.6f}"
        )

    if include_hror:
        result[HROR_UNIT] = hror_profile
    if return_diagnostics:
        return result, diagnostics
    return result


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================
def get_scenario_profiles(year: int = None) -> tuple:
    """
    Return scenario profiles and their associated probabilities.

    Returns
    -------
    profiles : dict
        ``{'low': (8760, 3), 'avg': (8760, 3), 'high': (8760, 3)}``.
    probabilities : dict
        ``{'low': float, 'avg': float, 'high': float}``.
    """
    scenarios = get_representative_scenarios(year)

    profiles = {
        'low': scenarios['low_yield']['profile'],
        'avg': scenarios['avg_yield']['profile'],
        'high': scenarios['high_yield']['profile']
    }

    probabilities = {
        'low': scenarios['low_yield']['probability'],
        'avg': scenarios['avg_yield']['probability'],
        'high': scenarios['high_yield']['probability']
    }

    return profiles, probabilities


def get_h2res_unit_profiles(year: int = None,
                            scenario: str = 'avg_yield__normal',
                            country_code: str = "HR",
                            wind_resolution_mode: str = "auto") -> dict:
    """
    Return decomposed per-unit capacity-factor profiles ready for injection
    into the H2RES optimisation model.

    Parameters
    ----------
    year : int, optional
        Planning year for climate-change scaling.
    scenario : str
        Scenario key such as ``'avg_yield__normal'`` or legacy one-axis keys
        like ``'avg_yield'``.

    Returns
    -------
    dict
        ``{unit_name: (8760,) array}`` for all active solar and wind units plus
        ``hror``.
    """
    scenarios = get_representative_scenarios(year)
    profile = scenarios[scenario]['profile']
    return decompose_to_h2res_units(
        profile,
        country_code=country_code,
        wind_resolution_mode=wind_resolution_mode,
    )


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("Scenario Manager Test (XGBoost Backend)")
    print("="*60 + "\n")

    # Generate scenarios
    scenarios = get_representative_scenarios(year=2025)

    # Print summary
    print("\nScenario Summary:")
    print("-" * 40)
    for name, data in scenarios.items():
        print(f"\n{name}:")
        print(f"  Profile shape: {data['profile'].shape}")
        print(f"  Probability: {data['probability']:.2%}")
        print(f"  Total energy: {data['total_energy']:.1f}")
        print(f"  Solar range: [{data['profile'][:, 0].min():.3f}, {data['profile'][:, 0].max():.3f}]")
        print(f"  Wind range: [{data['profile'][:, 1].min():.3f}, {data['profile'][:, 1].max():.3f}]")

    # Test decomposition
    print("\n" + "-" * 40)
    print("Unit Decomposition Test (avg_yield):")
    avg_profile = scenarios['avg_yield']['profile']
    unit_profiles = decompose_to_h2res_units(avg_profile)

    for unit_name, profile in unit_profiles.items():
        print(f"  {unit_name}: shape={profile.shape}, "
              f"range=[{profile.min():.4f}, {profile.max():.4f}], "
              f"mean={profile.mean():.4f}")

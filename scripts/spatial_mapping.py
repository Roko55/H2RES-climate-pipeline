"""Portable functions from the actual final H2RES input adapter."""
from pathlib import Path
import numpy as np
import pandas as pd
def _align_profile(values: np.ndarray, n: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) == n:
        return values
    if len(values) > n:
        return values[:n]
    reps = int(np.ceil(n / max(1, len(values))))
    return np.tile(values, reps)[:n]

def _hourly_mean_8760(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if not isinstance(work.index, pd.DatetimeIndex):
        if 'time' not in work.columns:
            raise ValueError('Spatial climate profiles require a DatetimeIndex or a time column')
        work['time'] = pd.to_datetime(work['time'], errors='coerce')
        work = work.dropna(subset=['time']).set_index('time')
    work = work.sort_index()
    if work.index.tz is not None:
        work.index = work.index.tz_localize(None)
    work = work.loc[~((work.index.month == 2) & (work.index.day == 29))]
    work = work.select_dtypes(include=[np.number])
    idx = work.index
    leap = idx.is_leap_year.astype(int)
    after_february = ((idx.month > 2) | (idx.month == 2) & (idx.day > 28)).astype(int)
    hour = (idx.dayofyear - leap * after_february - 1) * 24 + idx.hour
    hourly = work.assign(hour_of_year=hour).groupby('hour_of_year').mean(numeric_only=True)
    return hourly.reindex(np.arange(8760)).interpolate(limit_direction='both').ffill().bfill()

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

def _spatial_unit_profiles(target_profile: np.ndarray, source_path: Path, transfer_path: Path, unit_capacities: dict[str, float]) -> tuple[pd.DataFrame, dict]:
    transfer = pd.read_csv(transfer_path)
    required = {'unit_name', 'source_zone', 'weight'}
    if not required.issubset(transfer.columns):
        raise ValueError(f'Transfer matrix {transfer_path} is missing {sorted(required - set(transfer.columns))}')
    transfer['unit_name'] = transfer['unit_name'].astype(str).str.strip()
    transfer['source_zone'] = transfer['source_zone'].astype(str).str.strip()
    transfer['weight'] = pd.to_numeric(transfer['weight'], errors='coerce').fillna(0.0).clip(lower=0.0)
    units = list(unit_capacities)
    pivot = transfer.pivot_table(index='unit_name', columns='source_zone', values='weight', aggfunc='sum', fill_value=0.0).reindex(units, fill_value=0.0)
    if (pivot.sum(axis=1) <= 0).any():
        missing_units = pivot.index[pivot.sum(axis=1) <= 0].tolist()
        raise ValueError(f'Units without a spatial transfer mapping in {transfer_path}: {missing_units}')
    pivot = pivot.div(pivot.sum(axis=1), axis=0)
    spatial_history = pd.read_parquet(source_path)
    source_dates = pd.to_datetime(spatial_history['time'] if 'time' in spatial_history.columns else spatial_history.index)
    training_mask = source_dates.year <= 2015 if isinstance(source_dates, pd.DatetimeIndex) else source_dates.dt.year.le(2015)
    spatial_history = spatial_history.loc[training_mask].copy()
    if spatial_history.empty:
        raise ValueError('No training-period spatial observations available')
    source = _hourly_mean_8760(spatial_history)
    zones = [zone for zone in pivot.columns if zone in source.columns]
    if not zones:
        raise ValueError(f'No common source zones between {source_path} and {transfer_path}')
    pivot = pivot[zones]
    pivot = pivot.div(pivot.sum(axis=1), axis=0)
    caps = pd.Series(unit_capacities, dtype=float).reindex(units)
    unit_cap_weights = caps / float(caps.sum())
    zone_cap = pivot.mul(caps, axis=0).sum(axis=0)
    zone_weights = zone_cap / float(zone_cap.sum())
    historical = source[zones].astype(float).clip(0.0, 1.0)
    historical_aggregate = historical.mul(zone_weights, axis=1).sum(axis=1).to_numpy(dtype=float)
    ratios = np.divide(historical.to_numpy(dtype=float), historical_aggregate.reshape(-1, 1), out=np.ones_like(historical.to_numpy(dtype=float)), where=historical_aggregate.reshape(-1, 1) > 1e-08)
    target = _align_profile(target_profile, 8760)
    zone_profiles = _balance_weighted_profiles(ratios * target.reshape(-1, 1), zone_weights.to_numpy(), target)
    unit_profiles = zone_profiles @ pivot.to_numpy(dtype=float).T
    aggregate = unit_profiles @ unit_cap_weights.to_numpy(dtype=float)
    mass_error = float(np.mean(np.abs(aggregate - target)))
    return (pd.DataFrame(unit_profiles, columns=units), {'spatial_reference_end_year': 2015, 'spatial_reference_rows': len(spatial_history), 'source_zone_count': int(len(zones)), 'source_zones': zones, 'unit_count': int(len(units)), 'units': units, 'mass_mean_abs_error': mass_error, 'aggregate_mean_cf': float(np.mean(aggregate))})

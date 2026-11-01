"""Executed electricity/thermal numerical methods with explicit baseline inputs."""
from typing import Any
import numpy as np
import pandas as pd

def _preserve_total_under_cap(values: np.ndarray, target_total: float, cap: float) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if target_total <= 0.0 or cap <= 0.0:
        return np.zeros_like(values)
    capped = np.minimum(np.maximum(values, 0.0), cap)
    feasible_total = min(float(target_total), cap * len(capped))
    for _ in range(20):
        diff = feasible_total - float(capped.sum())
        if abs(diff) <= max(1e-08, feasible_total * 1e-10):
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
            capped[positive] = np.maximum(capped[positive] + capped[positive] * (diff / positive_sum), 0.0)
    return capped

def _reshape_prepared_temperature_demand(demand: dict[Any, float], periods: list[int], scenario_temp: np.ndarray, normal_temp: np.ndarray, mode: str, strength: float, peak_safe: bool) -> dict[str, float]:
    if not demand or not periods:
        return {'units_scaled': 0, 'total_error': 0.0, 'peak_ratio_max': 0.0}
    if mode == 'heat':
        scenario_driver = np.maximum(18.0 - scenario_temp, 0.0)
        normal_driver = np.maximum(18.0 - normal_temp, 0.0)
    elif mode == 'cool':
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
        if isinstance(key, tuple) and len(key) >= 2 and (int(key[1]) in factor_by_period):
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
    return {'units_scaled': units_scaled, 'total_error': total_error, 'peak_ratio_max': max(peak_ratios) if peak_ratios else 0.0}

def _theoretical_cop(temp_c: np.ndarray, sink_temp_c: float=45.0, carnot_efficiency: float=0.36, min_cop: float=1.2, max_cop: float=5.5) -> np.ndarray:
    sink_kelvin = float(sink_temp_c) + 273.15
    source_kelvin = np.asarray(temp_c, dtype=float) + 273.15
    temperature_lift = np.maximum(sink_kelvin - source_kelvin, 1.0)
    return np.clip(float(carnot_efficiency) * sink_kelvin / temperature_lift, min_cop, max_cop)

def _temperature_sensitive_cop(baseline_values: np.ndarray, temp_c: np.ndarray, baseline_temp_c: np.ndarray, sensitivity: float=0.85) -> np.ndarray:
    reference = _theoretical_cop(baseline_temp_c)
    current = _theoretical_cop(temp_c)
    ratio = np.divide(current, reference, out=np.ones_like(current), where=reference > 0.0)
    updated = np.asarray(baseline_values, dtype=float) * np.power(ratio, float(sensitivity))
    baseline_mean = float(np.nanmean(baseline_values))
    updated_mean = float(np.nanmean(updated))
    if baseline_mean > 0.0 and updated_mean > 0.0:
        updated *= baseline_mean / updated_mean
    return np.clip(updated, 1.2, 5.5)

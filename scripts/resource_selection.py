"""Final electricity/thermal scenario selection, with explicit array input."""
import numpy as np
from typing import Any
from sklearn.cluster import KMeans
CLIMATE_CHANGE={"base_year":2020,"target_year":2050,"solar_factor_2050":1.03,"wind_factor_2050":0.92,"temp_offset_2050":1.5}
def _apply_climate_change(profile: np.ndarray, planning_year: int) -> np.ndarray:
    out = np.asarray(profile, dtype=float).copy()
    base = float(CLIMATE_CHANGE['base_year'])
    target = float(CLIMATE_CHANGE['target_year'])
    frac = 0.0 if target == base else (float(planning_year) - base) / (target - base)
    frac = max(0.0, min(1.0, frac))
    out[:, 0] *= 1.0 + frac * (float(CLIMATE_CHANGE['solar_factor_2050']) - 1.0)
    out[:, 1] *= 1.0 + frac * (float(CLIMATE_CHANGE['wind_factor_2050']) - 1.0)
    out[:, 2] += frac * float(CLIMATE_CHANGE['temp_offset_2050'])
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
    return {names[0]: np.where(values <= q1)[0], names[1]: np.where((values > q1) & (values <= q2))[0], names[2]: np.where(values > q2)[0]}

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

def get_representative_scenarios(scenarios, planning_year: int=2020) -> dict[str, dict[str, Any]]:
    scenarios = np.stack([_apply_climate_change(profile, planning_year) for profile in scenarios])
    renewable_energy = scenarios[:, :, 0].sum(axis=1) + scenarios[:, :, 1].sum(axis=1)
    thermal = _thermal_index(scenarios)
    n = scenarios.shape[0]
    yield_clusters = _cluster_axis(renewable_energy, ('low_yield', 'avg_yield', 'high_yield'))
    thermal_clusters = _cluster_axis(thermal, ('cold', 'normal', 'hot'))
    out: dict[str, dict[str, Any]] = {}
    for name, idx in yield_clusters.items():
        rep = _representative_index(idx, renewable_energy)
        out[name] = {'profile': scenarios[rep], 'probability': float(len(idx) / n), 'cluster_size': int(len(idx)), 'energy': float(renewable_energy[rep]), 'thermal_index': float(thermal[rep])}
    for name, idx in thermal_clusters.items():
        rep = _representative_index(idx, thermal)
        out[name] = {'profile': scenarios[rep], 'probability': float(len(idx) / n), 'cluster_size': int(len(idx)), 'energy': float(renewable_energy[rep]), 'thermal_index': float(thermal[rep])}
    for yield_name in ('low_yield', 'avg_yield', 'high_yield'):
        for thermal_name in ('cold', 'normal', 'hot'):
            scenario_name = f'{yield_name}__{thermal_name}'
            idx = np.intersect1d(yield_clusters[yield_name], thermal_clusters[thermal_name])
            if idx.size == 0:
                y_center = np.mean(renewable_energy[yield_clusters[yield_name]])
                t_center = np.mean(thermal[thermal_clusters[thermal_name]])
                y_scale = np.std(renewable_energy) or 1.0
                t_scale = np.std(thermal) or 1.0
                combined_score = ((renewable_energy - y_center) / y_scale) ** 2 + ((thermal - t_center) / t_scale) ** 2
                rep = int(np.argmin(combined_score))
            else:
                combined_score = renewable_energy + thermal * 0.0
                rep = _representative_index(idx, combined_score)
            out[scenario_name] = {'profile': scenarios[rep], 'probability': float(idx.size / n), 'cluster_size': int(idx.size), 'energy': float(renewable_energy[rep]), 'thermal_index': float(thermal[rep])}
    return out

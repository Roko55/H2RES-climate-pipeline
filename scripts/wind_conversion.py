"""Explicit-unit wind conversion; no implicit power-curve extrapolation."""
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class PowerCurve:
    speeds_ms: tuple[float, ...]
    powers: tuple[float, ...]
    power_unit: str
    nominal_power: float
    nominal_power_unit: str
    zero_below_ms: float
    zero_above_ms: float
    source: str
    operating_mode: str
    reference_density_kg_m3: float

    def arrays(self):
        factors = {'W': 1., 'kW': 1000., 'MW': 1000000.}
        if self.power_unit not in factors or self.nominal_power_unit not in factors:
            raise ValueError('Power and nominal-power units must be explicit W, kW or MW')
        x = np.asarray(self.speeds_ms, dtype=float)
        y = np.asarray(self.powers, dtype=float)*factors[self.power_unit]
        nominal = self.nominal_power*factors[self.nominal_power_unit]
        if (x.ndim != 1 or y.shape != x.shape or len(x) < 2
            or not np.isfinite(x).all() or not np.isfinite(y).all()
            or (x < 0).any() or not (np.diff(x) > 0).all()
            or not math.isfinite(nominal) or nominal <= 0
            or (y < 0).any() or (y > nominal*(1+1e-10)).any()):
            raise ValueError('Invalid power-curve table or nominal power')
        if (not self.source or not self.operating_mode
            or not math.isfinite(self.reference_density_kg_m3) or self.reference_density_kg_m3 <= 0):
            raise ValueError('Source, operating mode and reference air density are required')
        if (not math.isfinite(self.zero_below_ms) or not math.isfinite(self.zero_above_ms)
            or not 0 <= self.zero_below_ms < self.zero_above_ms):
            raise ValueError('Explicit startup and shutdown thresholds are required')
        return x, y/nominal


def hub_speed(values, *, unit, source_height_m, hub_height_m, shear_exponent=None):
    speed = np.asarray(values, dtype=float)
    if unit != 'm/s':
        raise ValueError('Wind conversion requires m/s; unexpected source units must be reconciled first')
    if not np.isfinite(speed).all() or (speed < 0).any():
        raise ValueError('Wind speeds must be finite and nonnegative')
    if (not math.isfinite(source_height_m) or not math.isfinite(hub_height_m)
        or source_height_m <= 0 or hub_height_m <= 0):
        raise ValueError('Positive documented source and hub heights are required')
    if source_height_m == hub_height_m:
        return speed.copy()
    if shear_exponent is None or not math.isfinite(shear_exponent):
        raise ValueError('A declared shear treatment is required when heights differ')
    return speed*(hub_height_m/source_height_m)**shear_exponent


def capacity_factor(speed_at_hub, curve, *, speed_correction, unit='m/s'):
    if unit != 'm/s':
        raise ValueError('Power-curve input must be m/s')
    if not math.isfinite(speed_correction) or speed_correction <= 0:
        raise ValueError('A positive finite speed correction is required')
    speed = np.asarray(speed_at_hub, dtype=float)
    if not np.isfinite(speed).all() or (speed < 0).any():
        raise ValueError('Wind speeds must be finite and nonnegative')
    x, normalized = curve.arrays()
    corrected = speed*speed_correction
    active = (corrected >= curve.zero_below_ms) & (corrected <= curve.zero_above_ms)
    if ((corrected[active] < x[0]) | (corrected[active] > x[-1])).any():
        raise ValueError('Active wind speeds extend outside the documented curve support')
    result = np.zeros_like(corrected, dtype=float)
    result[active] = np.interp(corrected[active], x, normalized)
    return result


def plant_power_mw(cf, *, installed_mw, connection_limit_mw, availability=1., efficiency=1.):
    values = np.asarray(cf, dtype=float)
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError('Capacity factors must lie in [0, 1]')
    if (not math.isfinite(installed_mw) or installed_mw <= 0
        or not math.isfinite(connection_limit_mw) or connection_limit_mw < 0
        or not math.isfinite(availability) or not 0 <= availability <= 1
        or not math.isfinite(efficiency) or not 0 <= efficiency <= 1):
        raise ValueError('Invalid installed power, connection limit, availability or efficiency')
    return np.minimum(values*installed_mw*availability*efficiency, connection_limit_mw)


def energy_gwh(power_mw, *, interval_hours):
    values = np.asarray(power_mw, dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('Power must be finite nonnegative MW')
    if not math.isfinite(interval_hours) or interval_hours <= 0:
        raise ValueError('A positive interval duration is required')
    return float(values.sum()*interval_hours/1000.)

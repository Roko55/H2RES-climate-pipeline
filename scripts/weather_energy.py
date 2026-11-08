"""Pure weather-to-service conversion. No optimizer or server access."""
from __future__ import annotations

import numpy as np

HOURS = 8760


def vector(values):
    out = np.asarray(values, dtype=float)
    if out.shape != (HOURS,) or not np.isfinite(out).all():
        raise ValueError('Expected one finite 8760-hour weather year')
    return out


def signals(temperature):
    t = vector(temperature)
    # Cyclic history is explicit for an annual repeating weather profile.
    effective = sum(np.roll(t, step) for step in range(6)) / 6.
    return np.maximum(18. - t, 0.), np.maximum(effective - 22., 0.)


def heat_service(temperature, space_mwh, water_mwh, reference_hdd):
    if min(space_mwh, water_mwh) < 0 or reference_hdd <= 0:
        raise ValueError('Invalid reference heat service')
    hdd, _ = signals(temperature)
    return space_mwh / reference_hdd * hdd, np.full(HOURS, water_mwh / HOURS)


def cooling_service(temperature, reference_mwh, reference_cdd):
    if reference_mwh < 0 or reference_cdd <= 0:
        raise ValueError('Invalid cooling reference')
    _, cdd = signals(temperature)
    return reference_mwh / reference_cdd * cdd


def cop_curve(temperature, multiplier):
    t = vector(temperature)
    if not np.isfinite(multiplier) or multiplier <= 0:
        raise ValueError('Invalid common COP multiplier')
    lift = np.maximum(45. - t, 5.)
    return np.clip(multiplier * .36 * (45. + 273.15) / lift, 1.2, 5.5)


def calibrate_cop(temperature, reference_cop, heat_weights):
    """Match reference-year electricity under a shared heat-load proxy once."""
    weights, native = vector(heat_weights), vector(reference_cop)
    if (weights < 0).any() or weights.sum() <= 0 or (native <= 0).any():
        raise ValueError('Invalid COP calibration reference')
    target = float(np.sum(weights / native))
    lo, hi = .01, 10.
    if not np.sum(weights / cop_curve(temperature, hi)) <= target <= np.sum(weights / cop_curve(temperature, lo)):
        raise ValueError('Reference COP cannot be reproduced inside physical bounds')
    for _ in range(90):
        mid = (lo + hi) / 2.
        if np.sum(weights / cop_curve(temperature, mid)) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2., target

"""Validated adapters from annual climate arrays to H2RES parameter dictionaries.

These functions prepare inputs only; they do not import or invoke an optimizer.
Units and dictionaries are explicit so the interface can be used beyond the
country/unit names in the research application.
"""
from numbers import Integral
import numpy as np
import pandas as pd
try:
    # The installed namespace avoids colliding with an H2RES project's own
    # scripts package. Direct checkout use remains supported for the demo.
    from h2res_climate import temperature_coupling as thermal
except ModuleNotFoundError as error:
    if error.name != "h2res_climate":
        raise
    from scripts import temperature_coupling as thermal


def annual_array(values, label, capacity_factor=False):
    array = np.asarray(values, dtype=float)
    if array.shape != (8760,) or not np.isfinite(array).all():
        raise ValueError(f"{label} must contain exactly 8760 finite hourly values")
    if capacity_factor and ((array < 0).any() or (array > 1).any()):
        raise ValueError(f"{label} capacity factors must lie in [0, 1]")
    return array


def validate_year(year):
    if isinstance(year, bool) or not isinstance(year, Integral) or year < 1:
        raise ValueError("Planning year must be a positive integer")
    return int(year)


def availability_frame(unit_profiles, year, expected_units=None):
    """Return wide availability columns: year, period (1..8760), unit names."""
    year = validate_year(year)
    if not unit_profiles:
        raise ValueError("At least one generation-unit profile is required")
    if expected_units is not None and set(unit_profiles) != set(expected_units):
        raise ValueError("Exported unit names differ from the expected solver units")
    columns = {"year": np.full(8760, year), "period": np.arange(1, 8761)}
    for unit, values in unit_profiles.items():
        if not isinstance(unit, str) or not unit.strip() or unit in {"year", "period"}:
            raise ValueError("Use nonempty unit names distinct from year/period")
        columns[unit] = annual_array(values, unit, capacity_factor=True)
    return pd.DataFrame(columns)


def availability_parameters(unit_profiles, year, expected_units=None):
    """Return aval_factor_plant[(unit, period, year)] for a full annual horizon."""
    frame = availability_frame(unit_profiles, year, expected_units)
    return {(unit, period, int(year)): float(value)
            for unit in unit_profiles
            for period, value in enumerate(frame[unit].to_numpy(), start=1)}


def inject_availability(existing, generated):
    """Return a copy with climate entries updated; reject missing runtime keys.

    Unrelated units/years retain their existing values. No climate key is silently
    skipped, so mismatched solver names or period numbering are detected.
    """
    missing = set(generated) - set(existing)
    if missing:
        raise KeyError(f"Runtime lacks {len(missing)} generated unit/period/year keys")
    result = dict(existing)
    result.update(generated)
    return result


def couple_temperature(temp_profile, baseline_temp_profile, year, *,
                       heat_demand, cooling_demand, hp_cop_period,
                       district_heat_units=(), general_heat_units=(),
                       cooling_units=(), heat_pump_units=(), country_code="custom"):
    """Return coupled copies and diagnostics, preserving annual demand totals.

    Inputs/outputs use (unit, period, year) keys, periods 1..8760. Every selected
    unit must have a complete finite baseline. No padding, country-file lookup or
    mutation of caller dictionaries occurs. Unit groups must be explicit.
    """
    year = validate_year(year)
    temp = annual_array(temp_profile, "Temperature")
    baseline_temp = annual_array(baseline_temp_profile, "Baseline temperature")
    groups = [tuple(district_heat_units), tuple(general_heat_units),
              tuple(cooling_units), tuple(heat_pump_units)]
    if not any(groups):
        raise ValueError("Select at least one demand or heat-pump unit")
    if set(groups[0]) & set(groups[1]):
        raise ValueError("District and general heat unit groups must not overlap")
    for units, data, positive in ((groups[0]+groups[1], heat_demand, False),
                                  (groups[2], cooling_demand, False),
                                  (groups[3], hp_cop_period, True)):
        if len(units) != len(set(units)):
            raise ValueError("A selected unit group contains duplicate names")
        for unit in units:
            keys = [(unit, period, year) for period in range(1, 8761)]
            if any(key not in data for key in keys):
                raise KeyError(f"Incomplete 8760-hour baseline for {unit}")
            values = np.array([data[key] for key in keys], dtype=float)
            if not np.isfinite(values).all() or (values < 0).any() or (positive and (values <= 0).any()):
                raise ValueError(f"Invalid demand/COP baseline for {unit}")
    heat, cooling, cop = dict(heat_demand), dict(cooling_demand), dict(hp_cop_period)
    diagnostics = thermal.apply_temperature_coupling_to_runtime(
        temp, year, country_code, list(range(1, 8761)), heat, cooling, cop,
        heat_demand, cooling_demand, hp_cop_period,
        district_heat_units=groups[0], general_heat_units=groups[1],
        cooling_units=groups[2], atw_cop_units=groups[3],
        scale_annual_totals=False, baseline_temp_profile=baseline_temp)
    return {"heat_demand": heat, "cooling_demand": cooling,
            "hp_cop_period": cop, "diagnostics": diagnostics}

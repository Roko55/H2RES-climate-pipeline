"""Map one frozen national water resource to the calibrated future hydro inventory."""
import csv
from hashlib import sha256
import io
import json
from math import fsum, isfinite

PERIODS = tuple(range(1, 8761))
TOL = 1e-7
SERIES = {
    "reservoir": "reservoir_natural_turbine_equivalent_mwh",
    "open_pumped": "open_pumped_natural_turbine_equivalent_mwh",
    "run_of_river": "run_of_river_pondage_mwh",
}


def turbine_equivalent_coefficient(energy, nameplate, turbine_efficiency):
    if (not all(isfinite(x) for x in (energy, nameplate, turbine_efficiency))
            or not (energy >= 0 and nameplate > 0 and 0 < turbine_efficiency <= 1)):
        raise ValueError("Invalid hydro resource conversion inputs")
    return energy / (nameplate * turbine_efficiency ** 2)


def read_resource(data, manifest):
    meta = json.loads(manifest)
    if (meta["selected_climate_year"] != 1998
            or sha256(data).hexdigest() != meta["candidate_hourly_sha256"]):
        raise ValueError("Expected the pinned 1998 PECD resource")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    if tuple(int(row["period"]) for row in rows) != PERIODS:
        raise ValueError("Expected 8,760 ordered future hydro hours")
    result = {}
    for category, field in SERIES.items():
        values = tuple(float(row[field]) for row in rows)
        expected = 1000. * meta["selected_component_totals_gwh"][category]
        if (any(not isfinite(x) or x < 0 for x in values)
                or abs(fsum(values) - expected) > TOL):
            raise ValueError(f"Invalid natural hydro input for {category}")
        result[category] = values
    return result


def install_natural_inputs(ctx, resource):
    """Set the input coefficients before building, returning their absolute ledger."""
    years = tuple(ctx["years"])
    if not years or min(years) < 2025 or tuple(ctx["periods"]) != PERIODS or not ctx["hydro_storage"]:
        raise ValueError("The fixed PECD resource is for future full-year windows")
    groups = {
        "reservoir": ("hdam_units", "hdam_inflow", "hdam_efficiency", 1485.7),
        "open_pumped": ("hphs_units", "hphs_inflow", "hphs_efficiency", 275.4),
        "run_of_river": ("hror_units", "aval_factor_plant", None, 438.3),
    }
    prepared = {}
    for group, (family, field, efficiency, expected_capacity) in groups.items():
        names = tuple(ctx[family])
        caps = {name: float(ctx["max_load"][name]) for name in names}
        if (not names or len(caps) != len(names) or any(not isfinite(c) or c <= 0 for c in caps.values())
                or abs(fsum(caps.values()) - expected_capacity) > TOL):
            raise ValueError(f"Unreconciled hydro inventory for {group}")
        values = resource[group]
        if len(values) != len(PERIODS):
            raise ValueError(f"Missing hydro resource hours for {group}")
        for name, capacity in caps.items():
            fraction = capacity / expected_capacity
            eta = None if efficiency is None else float(ctx[efficiency][name])
            for period, total in zip(PERIODS, values):
                energy = total * fraction
                coefficient = (energy / capacity if eta is None else
                               turbine_equivalent_coefficient(energy, capacity, eta))
                for year in years:
                    prepared[(name, period, year)] = (field, coefficient, energy)
    for key, (field, coefficient, _) in prepared.items():
        ctx[field][key] = coefficient
    return prepared


def install_absolute_run_of_river(ctx, ledger):
    """Replace the capacity-scaled equality with separate water and turbine limits."""
    model = ctx["ucp"]
    model.update()
    rows = []
    for name in ctx["hror_units"]:
        for year in ctx["years"]:
            age = year - ctx["hor_yrs"][0]
            start = ctx["decom_start_old"][name] - 1
            rate = ctx["decom_rate"][name]
            initial = ctx["max_load"][name] * round((1 - rate) ** (max(start, age) - start), 3)
            new_start = ctx["decom_start_new"][name] - 1
            new_fraction = round((1 - rate) ** (max(new_start, 0) - new_start), 3)
            carry = ctx["cap_inv_cumulative"][name, year]
            investment = ctx["cap_inv"][name, year]
            for period in PERIODS:
                key = name, period, year
                row = ctx["max_output_hror"][key]
                generation = ctx["generation"][key]
                field, factor, water = ledger[key]
                if (field != "aval_factor_plant" or row.Sense != "="
                        or abs(row.RHS - factor * (initial + carry)) > TOL
                        or model.getCoeff(row, generation) != 1.
                        or abs(model.getCoeff(row, investment) + factor * new_fraction) > TOL
                        or model.getRow(row).size() != (1 if factor * new_fraction == 0 else 2)):
                    raise ValueError(f"Unexpected native run-of-river constraint {key}")
                rows.append((key, row, generation, investment, initial + carry, new_fraction, water))
    for (name, period, year), row, generation, investment, turbine, new_fraction, water in rows:
        model.chgCoeff(row, investment, 0.)
        row.RHS = water
        row.Sense = "<"
        model.addConstr(generation <= turbine + investment * new_fraction,
                        name=f"future_run_of_river_turbine[{name},{period},{year}]")
    model.update()


def audit_built_natural_inputs(ctx, ledger):
    """Check the actual LP right-hand sides against the absolute natural-water ledger."""
    model = ctx["ucp"]
    model.update()
    audit = {}
    for family, tag, first, follow in (
        ("hdam_units", "hdam", "storage_level_cap_0", "storage_level_balance"),
        ("hphs_units", "hphs", "hphs_storage_level_cap_0", "hphs_storage_level_period"),
    ):
        for name in ctx[family]:
            eta = ctx[f"{tag}_efficiency"][name]
            initial = .5 * ctx[f"{tag}_max_storage"][name]
            for year in ctx["years"]:
                terms = []
                residual = 0.
                for period in PERIODS:
                    key = name, period, year
                    field, _, energy = ledger[key]
                    row = ctx[first][name, year] if period == 1 else ctx[follow][key]
                    expected_rhs = energy / eta + (initial if period == 1 else 0.)
                    residual = max(residual, abs(row.RHS - expected_rhs))
                    if (field != f"{tag}_inflow" or row.Sense != "=" or residual > TOL
                            or model.getCoeff(row, ctx["storage_level"][key]) != 1.
                            or abs(model.getCoeff(row, ctx["generation"][key]) - 1. / eta) > TOL
                            or model.getCoeff(row, ctx["sto_out_flow"][key]) != 1.):
                        raise ValueError(f"Built water balance differs from its absolute ledger in {key}")
                    if period > 1 and model.getCoeff(row, ctx["storage_level"][name, period-1, year]) != -1.:
                        raise ValueError(f"Built storage continuity differs in {key}")
                    terms.append(energy)
                audit.setdefault(name, {})[year] = {
                    "annual_natural_turbine_equivalent_mwh": fsum(terms),
                    "maximum_water_balance_rhs_error_mwh": residual,
                }
    return audit

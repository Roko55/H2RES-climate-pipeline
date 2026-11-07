"""Initialize a production rolling path from an accepted no-build 2020 base."""
from itertools import product


HORIZON = (2020, 2025, 2030, 2035, 2040, 2045, 2050)
CAPITAL_FAMILIES = {
    "cap_inv": ("units",),
    "cap_inv_FC": ("FC_units",),
    "heat_pump_dh_cap": ("HeatPump_units", "chp_units"),
    "heat_pump_ind_cap": ("HeatPump_units",),
    "trad_boiler_ind_cap_inv": ("thermal_units",),
    "trad_boiler_dh_cap_inv": ("thermal_units", "chp_units"),
    "stat_sto_cap_inv": ("Stat_storage_unit",),
    "h2_sto_max_cap": ("H2_storage_unit",),
    "h2_electrolysis_max_cap": ("Elec_h2_units",),
}


def seed_no_build_investments(context, base_result):
    """Return the native investment dictionaries for the first 2025 window.

    Use the production unit sets. The observed existing fleet belongs in
    the production inventory, independently of these zero additions.
    """
    if (base_result["planning_approved"] is not True or base_result["solver_status"] != 2
            or not 0 <= base_result["maximum_new_capacity"] <= 1e-7):
        raise ValueError("Expected an accepted 2020 base with no new capacity")
    if tuple(context["hor_yrs"]) != HORIZON or context["i"] != 1 or context["years"][0] != 2025:
        raise ValueError("Expected the first 2025 window with the full 2020 horizon origin")

    expected = {family + "_cumulative" for family in CAPITAL_FAMILIES}
    expected.update("values_" + family for family in CAPITAL_FAMILIES)
    found = {name for name, value in context.items() if isinstance(value, dict)
             and (name.endswith("_cumulative") or name.startswith("values_"))}
    if found != expected:
        raise ValueError("Production investment dictionary layout changed")

    state = {}
    for family, dimensions in CAPITAL_FAMILIES.items():
        realized = "values_" + family
        cumulative = family + "_cumulative"
        keys = list(product(*(context[dimension] for dimension in dimensions)))
        empty_totals = {(*key, year): 0.0 for key in keys for year in HORIZON}
        if len(empty_totals) != len(keys) * len(HORIZON):
            raise ValueError(f"Duplicate production keys in {family}")
        if context[realized] or context[cumulative] != empty_totals:
            raise ValueError(f"Expected an unused production state in {family}")
        state[cumulative] = empty_totals
        state[realized] = {(*key, HORIZON[0]): 0.0 for key in keys}
    return state

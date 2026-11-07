"""Connect declared reversible hydro pumping to the future single-zone LP."""

from math import fsum, isfinite


TOL = 1e-7


def install_pumping(context, ratings):
    """Install grid pumping for specified (unit, year) operating ratings.

    Each rating is (grid pumping MW, turbine MW, pumping efficiency).
    The two ratings share the hour as a continuous time allowance. The
    caller must remove any corresponding exogenous pumping load and
    report grid charging from the returned variables exactly once.
    """
    years = tuple(context["years"])
    periods = tuple(context["periods"])
    units = {unit for unit, _ in ratings}
    if (not years or min(years) < 2025 or periods != tuple(range(1, 8761))
            or not context["hydro_storage"] or not units
            or not units <= set(context["hphs_units"])
            or set(ratings) != {(unit, year) for unit in units for year in years}
            or context["PrimaryReserve"] or context["SecondaryReserve"]):
        raise ValueError("Expected future hourly single-zone hydro without reserve constraints")
    for (unit, year), (pump, turbine, efficiency) in ratings.items():
        if (not all(isfinite(x) for x in (pump, turbine, efficiency))
                or pump <= 0 or not 0 < turbine <= context["max_load"][unit]
                or not 0 < efficiency <= 1):
            raise ValueError(f"Invalid physical ratings for {unit}/{year}")

    model = context["ucp"]
    model.update()
    spill_rows = context["ceep_demand"] if context["ceep_limit"] else {}
    if spill_rows and (not 0 <= context["ceep_parameter"] <= 1
                       or any(spill_rows[y].Sense != "<" for y in years)):
        raise ValueError("Unexpected native electricity spill cap")
    rows = []
    ends = {}
    for unit, year in ratings:
        terminal = context["hphs_storage_level_end"][unit, year]
        terminal_soc = context["storage_level"][unit, periods[-1], year]
        initial = .5 * context["hphs_max_storage"][unit]
        if (not isfinite(initial) or initial <= 0
                or terminal.Sense != ">" or model.getRow(terminal).size() != 1
                or model.getCoeff(terminal, terminal_soc) != 1 or terminal.RHS != initial):
            raise ValueError("Unexpected native reversible terminal storage row")
        ends[unit, year] = terminal
        for p in periods:
            key = unit, p, year
            water = (context["hphs_storage_level_cap_0"][unit, year] if p == 1
                     else context["hphs_storage_level_period"][key])
            electric = context["meet_demand"][p, year]
            generation = context["generation"][key]
            turbine_efficiency = context["hphs_efficiency"][unit]
            if (not 0 < turbine_efficiency <= 1 or water.Sense != "=" or electric.Sense != "="
                    or model.getCoeff(electric, generation) != 1
                    or model.getCoeff(water, context["storage_level"][key]) != 1
                    or abs(model.getCoeff(water, generation) - 1 / turbine_efficiency) > 1e-10):
                raise ValueError(f"Unexpected native grid or water balance for {key}")
            rows.append((key, electric, water, generation))

    charging = {key: model.addVar(lb=0., ub=ratings[key[0], key[2]][0],
                                 name=f"hydro_grid_pumping[{key[0]},{key[1]},{key[2]}]")
                for key, _, _, _ in rows}
    model.update()
    shared = {}
    for key, electric, water, generation in rows:
        unit, p, year = key
        pump, turbine, efficiency = ratings[unit, year]
        charge = charging[key]
        model.chgCoeff(electric, charge, -1.)
        model.chgCoeff(water, charge, -efficiency)
        if spill_rows:
            model.chgCoeff(spill_rows[year], charge, -float(context["ceep_parameter"]))
        shared[key] = model.addConstr(charge / pump + generation / turbine <= 1.,
                                     name=f"hydro_shared_hour[{unit},{p},{year}]")
    for terminal in ends.values():
        terminal.Sense = "="
    model.update()
    for key, electric, water, generation in rows:
        pump, turbine, efficiency = ratings[key[0], key[2]]
        charge = charging[key]
        row = shared[key]
        if (charge.LB != 0. or charge.UB != pump
                or model.getCoeff(electric, charge) != -1.
                or model.getCoeff(water, charge) != -efficiency
                or row.Sense != "<" or row.RHS != 1. or model.getRow(row).size() != 2
                or abs(model.getCoeff(row, charge) - 1. / pump) > TOL
                or abs(model.getCoeff(row, generation) - 1. / turbine) > TOL
                or (spill_rows and model.getCoeff(spill_rows[key[2]], charge) != -float(context["ceep_parameter"]))):
            raise ValueError(f"Built pumping balance differs from its ratings for {key}")
    if any(row.Sense != "=" for row in ends.values()):
        raise ValueError("Built reversible terminal stock is not cyclic")
    return charging


def audit_pumping_solution(context, ratings, charging):
    """Audit solved operating hours and the reversible storage ledger."""
    result = {}
    for (unit, year), (pump, turbine, eta_pump) in ratings.items():
        eta_turbine = context["hphs_efficiency"][unit]
        previous = initial = .5 * context["hphs_max_storage"][unit]
        grid, output, injected, outflow = [], [], [], []
        max_water_error = max_time_excess = 0.
        shared_hours = 0
        for period in context["periods"]:
            key = unit, period, year
            soc = float(context["storage_level"][key].X)
            charge = float(charging[key].X)
            generation = float(context["generation"][key].X)
            spill = float(context["sto_out_flow"][key].X)
            water = (context["hphs_storage_level_cap_0"][unit, year].RHS - initial if period == 1
                     else context["hphs_storage_level_period"][key].RHS)
            time_excess = charge / pump + generation / turbine - 1.
            residual = soc - previous + generation / eta_turbine + spill - charge * eta_pump - water
            if (not all(isfinite(x) for x in (soc, charge, generation, spill, water))
                    or min(charge, generation, spill) < -TOL or time_excess > TOL
                    or soc < initial - TOL or soc > 2. * initial + TOL or abs(residual) > TOL):
                raise ValueError(f"Invalid solved reversible hydro balance for {key}")
            previous = soc
            max_water_error = max(max_water_error, abs(residual))
            max_time_excess = max(max_time_excess, time_excess)
            shared_hours += charge > TOL and generation > TOL
            grid.append(charge)
            output.append(generation)
            injected.append(water)
            outflow.append(spill)
        if abs(previous - initial) > TOL:
            raise ValueError(f"Solved reversible terminal stock differs for {unit}/{year}")
        result.setdefault(unit, {})[year] = dict(
            grid_pumping_mwh=fsum(grid), electricity_generated_mwh=fsum(output),
            natural_inflow_hydraulic_mwh=fsum(injected), spill_hydraulic_mwh=fsum(outflow),
            maximum_water_balance_error_mwh=max_water_error, maximum_hour_fraction_excess=max_time_excess,
            hours_sharing_pumping_and_generation=shared_hours, terminal_stock_change_mwh=previous - initial,
        )
    return result

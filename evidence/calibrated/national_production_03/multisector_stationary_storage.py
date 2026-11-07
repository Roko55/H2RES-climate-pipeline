"""Bind the national stationary battery to separate energy and grid MW limits."""
from math import isclose, isfinite
import json
from pathlib import Path

from electricity_project_integration import digest_file


TOL = 1e-7
POLICY = Path('data/planning_storage/policy.json')


def load_storage_policy(project, frozen):
    path = (project / POLICY).resolve()
    if (not path.is_relative_to(project.resolve()) or not path.is_file()
            or digest_file(path) != frozen.get(POLICY.as_posix())):
        raise ValueError('Stationary battery policy is missing or changed')
    policy = json.loads(path.read_bytes())
    if policy.get('scope') != 'future_national_stationary_storage' or not policy.get('sources'):
        raise ValueError('Stationary battery policy lacks the expected scope or sources')
    return policy


def near(actual, expected):
    return isclose(actual, expected, rel_tol=0., abs_tol=TOL)


def number(value):
    if hasattr(value, 'size'):
        if value.size():
            raise ValueError('Battery carried stock must be constant before the new solve')
        value = value.getConstant()
    value = float(value)
    if not isfinite(value) or value < -TOL:
        raise ValueError('Battery carried stock must be finite and nonnegative')
    return value


def install_storage_retirement(ctx, policy):
    if set(ctx['Stat_storage_unit']) != set(policy['units']):
        raise ValueError('Stationary battery inventory differs from the declared policy')
    for unit, spec in policy['units'].items():
        life = spec['new_service_life_years']
        if not isinstance(life, int) or life <= 0:
            raise ValueError('Battery service life must be a positive whole year')
        ctx['Sta_sto_decom_start_new'][unit] = life
        ctx['Sta_sto_decom_rate'][unit] = 1.


def recurrence_keys(rows, units, periods, years):
    """Match Gurobi's keys to the native addConstrs loop order."""
    actual = set(rows)
    for period_first in (True, False):
        lookup = {(u,p,y): (p,y,u) if period_first else (u,p,y)
                  for u in units for p in periods[1:] for y in years}
        if set(lookup.values()) == actual:
            return lookup
    raise ValueError('Native battery recurrence does not cover the complete hourly scope')


def install_storage_limits(ctx, policy):
    """Attach and verify the power, cyclic energy and spill rows before solving."""
    model = ctx['ucp']
    periods, years = tuple(ctx['periods']), tuple(ctx['years'])
    units = tuple(ctx['Stat_storage_unit'])
    if (periods != tuple(range(1, 8761)) or units != tuple(policy['units'])
            or any(ctx.get(f'{kind}_reserve_StatStorage_units') for kind in ('primary', 'secondary'))):
        raise ValueError('Battery policy requires hourly national dispatch without battery reserve')
    model.update()
    recurrence = recurrence_keys(ctx['stat_storage_soc'], units, periods, years)
    rows = {}
    settings = {}
    for unit in units:
        spec = policy['units'][unit]
        eta = ctx['Sta_sto_eff'][unit]
        duration = spec['new_grid_discharge_duration_h']
        if not (0. < eta <= 1. and near(eta, spec['one_way_efficiency']) and isfinite(duration) and duration > 0.):
            raise ValueError('Battery conversion or duration does not match the declared scenario')
        rate = eta / duration
        for year in years:
            existing = spec['existing_by_year'][str(year)]
            energy, power = existing['energy_mwh'], existing['grid_power_mw']
            if not all(isfinite(v) and v >= 0. for v in (energy, power)) or bool(energy) != bool(power):
                raise ValueError('Invalid documented battery energy and power pair')
            carry = number(ctx['stat_sto_cap_inv_cumulative'][unit, year])
            legacy = number(ctx['Sta_sto_init_cap'][unit] * ctx['StaSto_Decom_ind'][year])
            invest = ctx['stat_sto_cap_inv'][unit, year]
            capex = spec['new_capex_model_units_per_mwh'][str(year)]
            investment_limit = ctx['stat_storage_inv_max'][unit, year]
            if (capex <= 0. or not near(invest.Obj, ctx['NPV'][year] * capex)
                    or investment_limit.Sense != '<' or not near(model.getCoeff(investment_limit, invest), 1.)
                    or not near(investment_limit.RHS, spec['maximum_new_mwh_per_anchor'])):
                raise ValueError(f'Unexpected battery investment price or ceiling for {year}')
            terminal = ctx['stat_storage_last_max'][unit, year]
            terminal.Sense = '='
            terminal.RHS = 0.
            settings[unit, year] = (energy, power, carry, rate, eta, invest)
            for period in periods:
                key = unit, period, year
                capacity = ctx['stat_storage_max'][key]
                charge, discharge, soc = (ctx[name][key] for name in ('stat_sto_in', 'stat_sto_out', 'stat_sto_soc'))
                if (capacity.Sense != '<' or not near(capacity.RHS, legacy + carry)
                        or not near(model.getCoeff(capacity, soc), 1.)
                        or not near(model.getCoeff(capacity, invest), -1.)):
                    raise ValueError(f'Unexpected native battery capacity row at {key}')
                capacity.RHS = energy + carry
                balance = ctx['meet_demand'][period, year]
                if not near(model.getCoeff(balance, charge), -1.) or not near(model.getCoeff(balance, discharge), eta):
                    raise ValueError(f'Unexpected native battery grid meter at {key}')
                soc_row = (ctx['stat_storage_soc_0'][unit, year] if period == 1
                           else ctx['stat_storage_soc'][recurrence[key]])
                if (soc_row.Sense != '=' or not near(soc_row.RHS, 0.)
                        or not near(model.getCoeff(soc_row, soc), 1.)
                        or not near(model.getCoeff(soc_row, charge), -eta)
                        or not near(model.getCoeff(soc_row, discharge), 1.)
                        or (period > 1 and not near(model.getCoeff(
                            soc_row, ctx['stat_sto_soc'][unit, period-1, year]), -1.))):
                    raise ValueError(f'Unexpected native battery energy balance at {key}')
                inverter = model.addConstr(charge + eta * discharge - rate * invest <= power + rate * carry,
                                           name=f'stationary_grid_power[{unit},{period},{year}]')
                rows[key] = inverter
                if ctx['ceep_limit']:
                    fix_spill_discharge(ctx, year, discharge, eta)
    if ctx['ceep_limit']:
        for year in years:
            for period in periods:
                fix_spill_discharge(ctx, year, ctx['ev_sto_out'][period, year], ctx['ev_Grid_eff'])
    model.update()
    for (unit, period, year), inverter in rows.items():
        key = unit, period, year
        energy, power, carry, rate, eta, invest = settings[unit, year]
        charge, discharge = ctx['stat_sto_in'][key], ctx['stat_sto_out'][key]
        terminal = ctx['stat_storage_last_max'][unit, year]
        if (inverter.Sense != '<' or not near(inverter.RHS, power + rate * carry)
                or not near(model.getCoeff(inverter, charge), 1.)
                or not near(model.getCoeff(inverter, discharge), eta)
                or not near(model.getCoeff(inverter, invest), -rate)
                or not near(ctx['stat_storage_max'][key].RHS, energy + carry)
                or terminal.Sense != '=' or not near(terminal.RHS, 0.)
                or not near(model.getCoeff(terminal, ctx['stat_sto_soc'][unit, periods[-1], year]), 1.)):
            raise ValueError(f'Battery power, energy or terminal row did not install at {key}')
        if ctx['ceep_limit']:
            spill = ctx['ceep_demand'][year]
            ev = ctx['ev_sto_out'][period, year]
            if (not near(model.getCoeff(spill, discharge), ctx['ceep_parameter'] * eta)
                    or not near(model.getCoeff(spill, ev), ctx['ceep_parameter'] * ctx['ev_Grid_eff'])):
                raise ValueError(f'Spill denominator does not use grid-meter discharge at {key}')
    ctx['_storage_grid_policy'] = settings
    return dict(hours=len(rows), cyclic=True, spill_grid_meter=bool(ctx['ceep_limit']),
                existing={f'{unit}/{year}': dict(energy_mwh=v[0], grid_power_mw=v[1])
                          for (unit, year), v in settings.items()})


def fix_spill_discharge(ctx, year, variable, efficiency):
    row, model, share = ctx['ceep_demand'][year], ctx['ucp'], ctx['ceep_parameter']
    if not near(model.getCoeff(row, variable), share / efficiency):
        raise ValueError(f'Unexpected spill denominator in {year}')
    model.chgCoeff(row, variable, share * efficiency)


def audit_storage_solution(ctx):
    if ctx['ucp'].Status != 2:
        raise ValueError('Expected an optimal battery dispatch')
    summary = {}
    for (unit, year), settings in ctx['_storage_grid_policy'].items():
        energy, power, carry, rate, eta, invest = settings
        capacity = energy + carry + invest.X
        limit = power + rate * (carry + invest.X)
        maximum_power, overlap, previous = 0., 0., 0.
        for period in ctx['periods']:
            key = unit, period, year
            charge, withdrawal, soc = (ctx[name][key].X for name in ('stat_sto_in', 'stat_sto_out', 'stat_sto_soc'))
            discharge = eta * withdrawal
            if (not all(isfinite(v) and v >= -TOL for v in (charge, withdrawal, soc))
                    or charge + discharge > limit + TOL or soc > capacity + TOL
                    or not near(soc, previous + eta * charge - withdrawal)):
                raise ValueError(f'Battery dispatch violates physical bounds at {key}')
            maximum_power = max(maximum_power, charge + discharge)
            overlap = max(overlap, min(charge, discharge))
            previous = soc
        if not near(previous, 0.) or overlap > TOL:
            raise ValueError(f'Battery dispatch is not cyclic or has simultaneous opposite flows in {year}')
        summary[f'{unit}/{year}'] = dict(energy_mwh=capacity, grid_power_mw=limit,
                                        peak_grid_throughput_mw=maximum_power, peak_overlap_mw=overlap)
    return summary

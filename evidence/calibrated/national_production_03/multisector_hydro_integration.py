"""Stage the absolute water inputs and reversible fleet for the national planning model."""
import json
from math import fsum, isfinite
from pathlib import Path

from electricity_project_integration import digest_file
from future_absolute_hydro import (
    TOL, read_resource, install_natural_inputs, install_absolute_run_of_river, audit_built_natural_inputs,
)
from future_reversible_hydro import install_pumping, audit_pumping_solution


DATA = Path('data/planning_hydro')
HOURLY = DATA/'candidate_hourly.csv'
MANIFEST = DATA/'preflight.json'
POLICY = DATA/'policy.json'
NATIVE_CAPEX = 'Fossil_hydro       =  disp_units + hydro_units'
UNIQUE_CAPEX = 'Fossil_hydro       =  list(dict.fromkeys(disp_units + hydro_units))'


def load_hydro_inputs(project, frozen):
    files = {}
    for relative in (HOURLY, MANIFEST, POLICY):
        path = (project/relative).resolve()
        if (not path.is_relative_to(project.resolve()) or not path.is_file()
                or digest_file(path) != frozen.get(relative.as_posix())):
            raise ValueError('Future hydro input is missing or changed: '+str(relative))
        files[relative] = path.read_bytes()
    policy = json.loads(files[POLICY])
    if policy.get('scope') != 'future_national_hydro' or not policy.get('sources'):
        raise ValueError('Future hydro policy lacks scope or provenance')
    return read_resource(files[HOURLY], files[MANIFEST]), policy


def render_unique_hydro_capex(source):
    if source.count(NATIVE_CAPEX) != 1:
        raise ValueError('Expected one known duplicated native hydro objective expression')
    source = source.replace(NATIVE_CAPEX, UNIQUE_CAPEX)
    compile(source, '<future hydro CAPEX>', 'exec')
    return source


def prepare_hydro_window(ctx, water):
    originals = {name: ctx[name] for name in ('hdam_inflow','hphs_inflow','aval_factor_plant')}
    ctx.update({name: values.copy() for name,values in originals.items()})
    ctx['_hydro_originals'] = originals
    ctx['_hydro_natural_ledger'] = install_natural_inputs(ctx,water)


def install_and_audit_hydro(ctx, policy):
    model = ctx['ucp']
    model.update()
    ledger = ctx['_hydro_natural_ledger']
    built = audit_built_natural_inputs(ctx,ledger)
    install_absolute_run_of_river(ctx,ledger)
    names = tuple(ctx['hdam_units']) + tuple(ctx['hphs_units']) + tuple(ctx['hror_units'])
    if (set(names) != set(ctx['hydro_units']) or not set(names) <= set(ctx['Fossil_hydro'])
            or any(ctx['Fossil_hydro'].count(unit) != 1 for unit in names)):
        raise ValueError('Hydro investment cost must count each technology once')
    reservoir = policy['reservoir_stock_mwh']
    reversible = policy['reversible_units']
    if set(reservoir) != set(ctx['hdam_units']) or set(reversible) != set(ctx['hphs_units']):
        raise ValueError('Expected the approved reservoir and reversible unit scope')
    for unit in names:
        for year in ctx['years']:
            investment = ctx['cap_inv'][unit,year]
            price = ctx['NPV'][year] * ctx['cap_inv_cost'][unit]
            if not isfinite(price) or price <= 0. or abs(investment.Obj - price) > TOL:
                raise ValueError(f'Native hydro investment coefficient differs from its input in {unit}/{year}')
    for unit, storage in reservoir.items():
        if not isfinite(storage) or storage <= 0. or abs(ctx['hdam_max_storage'][unit]-storage) > TOL:
            raise ValueError('Reservoir storage differs from the physical inventory: '+unit)
        for year in ctx['years']:
            row = ctx['storage_level_end'][unit,year]
            soc = ctx['storage_level'][unit,ctx['periods'][-1],year]
            if (row.Sense != '>' or row.RHS != .5*storage or model.getRow(row).size() != 1
                    or model.getCoeff(row,soc) != 1.):
                raise ValueError(f'Unexpected reservoir terminal constraint {unit}/{year}')
            row.Sense = '='
    ratings = {}
    for unit, values in reversible.items():
        if abs(ctx['hphs_max_storage'][unit]-values['energy_mwh']) > TOL or ctx['max_inv_period'][unit] != 0.:
            raise ValueError('Reversible stock differs from the physical inventory: '+unit)
        for year in ctx['years']:
            ratings[unit,year] = values['pump_grid_mw'], values['turbine_mw'], values['pump_efficiency']
    ctx['_hydro_pump_ratings'] = ratings
    ctx['hydro_grid_pumping'] = install_pumping(ctx,ratings)
    if any(ctx['storage_level_end'][u,y].Sense != '=' for u in reservoir for y in ctx['years']):
        raise ValueError('Reservoir terminal stock is not cyclic')
    return dict(natural_water=built, reversible_hours=len(ctx['hydro_grid_pumping']),
                reservoir_ends_cyclic=True, capex_once=True)


def audit_hydro_solution(ctx):
    if ctx['ucp'].Status != 2:
        raise ValueError('Expected an optimal hydro dispatch')
    pumping = audit_pumping_solution(ctx,ctx['_hydro_pump_ratings'],ctx['hydro_grid_pumping'])
    reservoir, ror = {}, {}
    ledger = ctx['_hydro_natural_ledger']
    for year in ctx['years']:
        for unit in ctx['hdam_units']:
            capacity = ctx['hdam_max_storage'][unit]
            previous = initial = .5 * capacity
            eta = ctx['hdam_efficiency'][unit]
            out, spill = [], []
            max_error = 0.
            for period in ctx['periods']:
                key = unit,period,year
                soc, output, lost = (ctx[name][key].X for name in ('storage_level','generation','sto_out_flow'))
                error = abs(soc - previous - ledger[key][2]/eta + output/eta + lost)
                if (not all(isfinite(v) for v in (soc,output,lost)) or min(output,lost) < -TOL
                        or soc < initial-TOL or soc > capacity+TOL or error > TOL):
                    raise ValueError(f'Reservoir dispatch violates its water ledger at {key}')
                out.append(output)
                spill.append(lost)
                previous = soc
                max_error = max(max_error,error)
            if abs(previous-initial) > TOL:
                raise ValueError(f'Reservoir terminal stock does not close for {unit}/{year}')
            reservoir[f'{unit}/{year}'] = dict(generation_mwh=fsum(out), spill_hydraulic_mwh=fsum(spill),
                                               maximum_water_balance_error_mwh=max_error)
        for unit in ctx['hror_units']:
            values = []
            for period in ctx['periods']:
                key = unit,period,year
                output = ctx['generation'][key].X
                if not isfinite(output) or not -TOL <= output <= ledger[key][2]+TOL:
                    raise ValueError(f'Run-of-river output exceeds natural water at {key}')
                values.append(output)
            ror[f'{unit}/{year}'] = dict(generation_mwh=fsum(values))
    return dict(reservoir=reservoir, run_of_river=ror, pumping=pumping)

"""Separate biomass, biogas and geothermal dispatch in the future national model."""

from hashlib import sha256
import json
from math import fsum, isclose, isfinite


POLICY = 'data/planning_power/dispatchable_renewables.json'
UNITS = {'TEBiomassHR': 'Biomass', 'HRBiogas2020': 'Biogas', 'HRGeothermal2020': 'Geothermal'}
TOL = 1e-7


def load_dispatchable_renewables(root, frozen):
    payload = (root/POLICY).read_bytes()
    if sha256(payload).hexdigest() != frozen[POLICY]:
        raise ValueError('Dispatchable renewables policy changed')
    units = json.loads(payload)['units']
    if set(units) != set(UNITS):
        raise ValueError('Unexpected dispatchable renewables scope')
    for unit, item in units.items():
        if (item['fuel'] != UNITS[unit] or not 0. < item['net_electric_efficiency'] <= 1.
                or not 0. <= item['annual_max_mwh'] <= 8760*item['installed_mw']
                or item['active'] != (item['annual_max_mwh'] > 0.)):
            raise ValueError('Invalid dispatchable renewables policy: '+unit)
    return units


def apply_dispatchable_renewables(rows, units):
    if not set(units) <= {r['unit_name'] for r in rows}:
        raise ValueError('National inventory lacks its three dispatchable renewables')
    for row in rows:
        unit = row['unit_name']
        if unit in units:
            item = units[unit]
            row.update(fuel_type=item['fuel'], cap_mw=str(item['installed_mw']),
                       efficiency=str(item['net_electric_efficiency']), CO2Intensity='0.0',
                       cap_factor='1.0' if item['active'] else '0.0', max_inv_period='0.0',
                       final_life_cap='1.0')


def render_dispatchable_renewables_reader(source):
    fossil = "(df['fuel_type']=='Biomass')]['unit_name'])"
    if source.count(fossil) != 2:
        raise ValueError('Unexpected native thermal and biomass groups')
    source = source.replace(fossil, "(df['fuel_type'].isin(['Biomass','Biogas','Geothermal']))]['unit_name'])")
    fuel = 'fuel_cost_long     = fuel_cost.melt('
    if source.count(fuel) != 1:
        raise ValueError('Missing native fuel table reshape')
    return source.replace(fuel, "if 'Biogas' not in fuel_cost.columns:\n"
                                "    raise ValueError('Missing legacy Biogas fuel price')\n"
                                "fuel_cost['Geothermal'] = 0.0\n"+fuel)


def audit_and_limit_dispatchable_renewables(ctx, units):
    model, periods = ctx['ucp'], tuple(ctx['periods'])
    model.update()
    for unit, item in units.items():
        if (unit not in ctx['fossil_units'] or unit not in ctx['biomass_units']
                or ctx['CO2_factor'][unit] != 0. or ctx['max_load'][unit] != item['installed_mw']
                or ctx['cap_factor'][unit] != float(item['active']) or ctx['max_inv_period'][unit] != 0.):
            raise ValueError('Dispatchable renewable native settings differ: '+unit)
        for year in ctx['years']:
            carbon = ctx['CO2_emissions_power'][year]
            generation = ctx['generation']
            for hour in periods:
                key = (unit,hour,year)
                if (not isfinite(ctx['var_cost'][key]) or ctx['var_cost'][key] < 0.
                        or model.getCoeff(carbon,generation[key]) != 0.
                        or not isclose(generation[key].Obj,ctx['NPV'][year]*ctx['var_cost'][key],
                                       rel_tol=1e-10,abs_tol=1e-9)):
                    raise ValueError(f'Dispatchable renewable cost or fossil carbon row differs for {key}')
            annual = ctx['gp'].quicksum(generation[unit,hour,year] for hour in periods)
            model.addConstr(annual <= item['annual_max_mwh'],name=f'fuel_budget_{unit}_{year}')
    model.update()
    return dict(annual_max_mwh={u:item['annual_max_mwh'] for u,item in units.items()},
                years=list(ctx['years']), hourly_cost_and_fossil_co2_audited=True)


def audit_solved_dispatchable_renewables(ctx, units):
    totals = {}
    for year in ctx['years']:
        totals[int(year)] = {}
        for unit, item in units.items():
            output = fsum(ctx['generation'][unit,hour,year].X for hour in ctx['periods'])
            if not -TOL <= output <= item['annual_max_mwh']+TOL:
                raise ValueError(f'Dispatchable renewable exceeds resource limit: {unit}/{year}')
            totals[int(year)][unit] = output
    return dict(generation_mwh=totals)

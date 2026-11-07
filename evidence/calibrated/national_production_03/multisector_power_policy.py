"""Pin the common V6 policy and verify the native new-gas coefficients."""

import csv
from hashlib import sha256
import json
from math import fsum, isclose, isfinite


GAS_ROWS = 'data/planning_power/gas_stock.csv'
GAS_MANIFEST = 'data/planning_power/gas_stock_evidence.json'
NEW_UNIT = 'HRNewGasCCGT'
HEAT_UNIT = 'TE-TOZagreb'
POLICY_FLAGS = {'rps_inv': False, 'carbonLimit': False, 'res_inv': True}


def load_gas_stock(root, hashes):
    for name in (GAS_ROWS, GAS_MANIFEST):
        if sha256((root/name).read_bytes()).hexdigest() != hashes[name]:
            raise ValueError('Gas policy input changed: '+name)
    policy = json.loads((root/GAS_MANIFEST).read_bytes())
    with (root/GAS_ROWS).open(encoding='utf8', newline='') as stream:
        records = list(csv.DictReader(stream))
    indexed = {r['unit_name']: r for r in records}
    if (policy['output_sha256'] != hashes[GAS_ROWS] or len(indexed) != len(records)
            or any(r['fuel_type'] != 'Gas' for r in records)
            or fsum(float(r['cap_mw']) for r in records) != policy['available_gas_candidate_mw']):
        raise ValueError('Gas policy stock does not reconcile')
    new = indexed[NEW_UNIT]
    expansion = policy['expansion']
    if (expansion['unit'] != NEW_UNIT or float(new['cap_mw']) != 0.
            or new['CHPType'] != 'N' or indexed[HEAT_UNIT]['CHPType'] != 'Y'
            or float(new['max_inv_period']) != expansion['max_mw_per_anchor']
            or float(new['cap_inv_cost']) != expansion['capex_model_units_per_mw']
            or float(new['efficiency']) != expansion['net_electric_efficiency']
            or float(new['life_time']) != expansion['lifetime_years']
            or any(float(r['max_inv_period']) != 0. for u,r in indexed.items() if u != NEW_UNIT)):
        raise ValueError('Invalid new gas investment policy')
    return indexed, expansion


def audit_native_power_policy(ctx, stock):
    records, expansion = stock
    flags = {key: bool(ctx[key]) for key in POLICY_FLAGS}
    if flags != POLICY_FLAGS or 'rps_lim' in ctx or 'CO2_emissions_limit' in ctx:
        raise ValueError('Native renewable or emissions ceiling differs from the common V6 policy')
    model = ctx['ucp']
    if set(ctx['chp_units']) != {HEAT_UNIT}:
        raise ValueError('Gas expansion must not create a new district heat market')
    units = set(ctx['fossil_units'])
    if not records.keys() <= units:
        raise ValueError('Gas stock is missing from the native dispatch group')
    model.update()
    limits = {}
    for year in ctx['years']:
        if ctx['CO2_price'][year] <= 0.:
            raise ValueError('Carbon price must remain active')
        for unit, record in records.items():
            key = (unit,year)
            investment = ctx['cap_inv'][key]
            cap_rows = ctx['max_inv_unit']
            cap = cap_rows[key] if key in cap_rows else cap_rows[year,unit]
            ceiling = float(record['max_inv_period'])
            if (ctx['max_load'][unit] != float(record['cap_mw']) or cap.RHS != ceiling
                    or model.getCoeff(cap,investment) != 1.):
                raise ValueError(f'Gas stock or investment row differs for {unit}/{year}')
            if unit == HEAT_UNIT:
                continue
            factor = expansion['gas_co2_t_per_mwh_fuel']/float(record['efficiency'])
            if not isclose(ctx['CO2_factor'][unit],factor,rel_tol=1e-10):
                raise ValueError(f'Gas fuel and CO2 coefficients differ for {unit}')
            co2_row = ctx['CO2_emissions_power'][year]
            for period in ctx['periods']:
                generation = ctx['generation'][unit,period,year]
                expected = ctx['NPV'][year]*(ctx['var_cost'][unit,period,year]+ctx['CO2_price'][year]*factor)
                if (not isclose(model.getCoeff(co2_row,generation),factor,rel_tol=1e-10)
                        or not isclose(generation.Obj,expected,rel_tol=1e-10,abs_tol=1e-9)):
                    raise ValueError(f'Gas carbon accounting or dispatch cost differs for {unit}/{year}/{period}')
        investment = ctx['cap_inv'][NEW_UNIT,year]
        expected = ctx['NPV'][year]*expansion['capex_model_units_per_mw']
        if not isclose(investment.Obj,expected,rel_tol=1e-10):
            raise ValueError(f'New gas investment cost differs for {year}')
        limit = expansion['max_mw_per_anchor'] if year >= expansion['earliest_model_year'] else 0.
        investment.UB = limit
        limits[int(year)] = limit
    model.update()
    return dict(policy_flags=flags, carbon_price_active=True,
                initial_gas_mw=fsum(float(r['cap_mw']) for r in records.values()),
                new_gas_limit_mw=limits, hourly_fuel_co2_objective_audited=True)


def audit_solved_power_policy(ctx):
    emissions = {}
    native = {'power':'CO2_total', 'heat':'CO2_total_heat', 'industry':'CO2_total_industry'}
    for year in ctx['years']:
        sector = {key:ctx[name][year].X for key,name in native.items()}
        if any(not isfinite(n) or n < -1e-7 for n in sector.values()):
            raise ValueError('Invalid direct CO2 output for '+str(year))
        emissions[int(year)] = sector
    return dict(direct_co2_t_by_year=emissions)

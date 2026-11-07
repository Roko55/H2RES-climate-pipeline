"""Reconcile the national model's Zagreb heat market and storage equations."""
import json
from math import fsum, isclose, isfinite
from pathlib import Path

from electricity_project_integration import digest_file


POLICY = Path('data/planning_heat/district_policy.json')
SITE = 'TE-TOZagreb'
BOILER = 'gas_boilers'
PERIODS = tuple(range(1, 8761))
TOL = 1e-7


def close(a, b):
    return isclose(a, b, rel_tol=0., abs_tol=TOL)


def load_district_heat_policy(project, frozen):
    path = (project / POLICY).resolve()
    if (not path.is_relative_to(project.resolve()) or not path.is_file()
            or digest_file(path) != frozen.get(POLICY.as_posix())):
        raise ValueError('District heating policy is missing or changed')
    policy = json.loads(path.read_bytes())
    if (policy.get('scope') != 'te_to_zagreb_space_heating' or policy.get('unit') != SITE
            or not policy.get('sources') or any(not isfinite(policy[k]) or policy[k] <= 0.
                 for k in ('chp_space_heat_mw','boiler_stock_mw','storage_mwh','storage_power_mw'))):
        raise ValueError('Unexpected district heating policy')
    return policy


def prepare_district_heat(ctx, policy):
    if (tuple(ctx['chp_units']) != (SITE,) or ctx['NoResToHeatInv']
            or not close(ctx['STOCapacity_heat'][SITE], policy['storage_mwh'])):
        raise ValueError('Expected the single physical TE-TO Zagreb heat market')
    ctx['CHPMaxHeat'][SITE] = policy['chp_space_heat_mw']
    ctx['HP_Boiler_dh_init_cap'][BOILER,SITE] = policy['boiler_stock_mw']
    for year in ctx['years']:
        share = policy['retained_boiler_share'][str(year)]
        if not 0. <= share <= 1.:
            raise ValueError('Invalid retained district boiler share')
        ctx['Thermal_Decom_DH'][year] = share


def install_district_heat_rows(ctx, policy):
    model = ctx['ucp']
    model.update()
    if tuple(ctx['periods']) != PERIODS:
        raise ValueError('Expected the full hourly district heat window')
    stock0 = policy['storage_mwh']/2.
    power = policy['storage_power_mw']
    new_rows = []
    for year in ctx['years']:
        initial = ctx['heat_storage_level0'][SITE,year]
        expected_boiler = policy['boiler_stock_mw']*policy['retained_boiler_share'][str(year)]
        expected_boiler += ctx['trad_boiler_dh_cap_inv_cumulative'][BOILER,SITE,year]
        if hasattr(expected_boiler,'getValue'):
            expected_boiler = expected_boiler.getValue()
        prior = stock0
        for period in PERIODS:
            key = SITE,period,year
            row = initial if period == 1 else ctx['heat_storage_level'][key]
            stock, chp, served = (ctx[name][key] for name in ('heat_sto_level','heat_input','heat_gen'))
            hp = [ctx['heat_pump_dh'][(unit,*key)] for unit in ctx['HeatPump_units']]
            boilers = [ctx['boiler_dh_generation'][(unit,*key)] for unit in ctx['thermal_units']]
            if (row.Sense != '=' or row.RHS != (stock0 if period == 1 else 0.)
                    or model.getCoeff(row,stock) != 1. or model.getCoeff(row,chp) != -1.
                    or model.getCoeff(row,served) != 1.
                    or any(model.getCoeff(row,v) != (0. if period == 1 else -1.) for v in hp+boilers)
                    or (period > 1 and model.getCoeff(row,prior) != -1.)):
                raise ValueError(f'Unexpected district heat ledger at {key}')
            if period == 1:
                for v in hp+boilers:
                    model.chgCoeff(row,v,-1.)
            maximum = ctx['max_heat_sto_level'][key]
            peak = ctx['max_heat_gen'][key]
            boiler = ctx['boiler_max_prod_dh'][(BOILER,*key)]
            if (maximum.RHS != policy['storage_mwh'] or model.getCoeff(maximum,stock) != 1.
                    or peak.RHS != policy['chp_space_heat_mw'] or model.getCoeff(peak,chp) != 1.
                    or not close(boiler.RHS,expected_boiler)):
                raise ValueError(f'District heat asset rating did not reach the model at {key}')
            new_rows.extend((model.addConstr(stock-prior<=power, name=f'dh_store_charge[{period},{year}]'),
                             model.addConstr(prior-stock<=power, name=f'dh_store_discharge[{period},{year}]')))
            prior = stock
        model.addConstr(prior==stock0, name=f'dh_store_cyclic[{year}]')
        for unit in ctx['HeatPump_units']:
            terminal = ctx['heat_pump_soc_last'][unit,year]
            terminal.Sense = '='
            terminal.RHS = 0.
    model.update()
    if any(row.Sense != '<' for row in new_rows):
        raise ValueError('District heat power limits failed to install')
    for year in ctx['years']:
        key = SITE,1,year
        row = ctx['heat_storage_level0'][SITE,year]
        if (any(model.getCoeff(row,ctx['heat_pump_dh'][(unit,*key)]) != -1. for unit in ctx['HeatPump_units'])
                or any(model.getCoeff(row,ctx['boiler_dh_generation'][(unit,*key)]) != -1. for unit in ctx['thermal_units'])):
            raise ValueError('First-hour heat inputs did not reach the storage balance')
    return dict(storage_power_rows=len(new_rows), district_initial_and_terminal_mwh=stock0,
                first_hour_inflows_verified=True, boiler_stock_mw=policy['boiler_stock_mw'])


def install_district_fuel(ctx, policy):
    """Use one fuel basis for hourly CHP electricity, space heat and emissions."""
    model, gp = ctx['ucp'], ctx['gp']
    model.update()
    fuel = policy['fuel']
    eta, beta, boiler_eta, carbon, total_eta = (fuel[k] for k in (
        'chp_effective_efficiency','heat_extraction_coefficient','boiler_efficiency',
        'gas_co2_t_per_mwh_fuel','maximum_total_efficiency'))
    if (not all(isfinite(x) and 0. < x <= 1. for x in (eta,beta,boiler_eta,carbon,total_eta))
            or not close(ctx['CHPPowerLossFactor'][SITE],beta)):
        raise ValueError('Unexpected district fuel or thermal extraction parameters')
    power, heat, emissions, checks = [], [], [], []
    for year in ctx['years']:
        power_row, heat_row = ctx['CO2_emissions_power'][year], ctx['CO2_emissions_heat'][year]
        weight, carbon_price = ctx['NPV'][year], ctx['CO2_price'][year]
        for period in PERIODS:
            key = SITE,period,year
            p, q = ctx['generation'][key], ctx['heat_input'][key]
            v = ctx['boiler_dh_generation'][BOILER,SITE,period,year]
            gas = ctx['industry_fuels_price'][period,year,'Gas']
            p_cost, v_cost = ctx['var_cost'][key], ctx['heat_var_cost'][BOILER,period,year]
            p_co2 = model.getCoeff(power_row,p)
            if (not all(isfinite(x) and x >= 0. for x in (gas,weight,carbon_price,p_cost,v_cost,p_co2))
                    or not close(p.Obj, weight*(p_cost+carbon_price*p_co2)) or q.Obj != 0.
                    or not close(v.Obj, weight*v_cost) or model.getCoeff(heat_row,q) != 0.):
                raise ValueError(f'Unexpected original district fuel cost or emissions at {key}')
            power.extend((weight*(gas/eta-p_cost)*p, weight*gas*beta/eta*q))
            heat.append(weight*(gas/boiler_eta-v_cost)*v)
            emissions.append(weight*carbon_price*(carbon/eta-p_co2)*p)
            model.chgCoeff(power_row,p,carbon/eta)
            model.chgCoeff(heat_row,q,carbon*beta/eta)
            model.chgCoeff(heat_row,v,carbon/boiler_eta)
            bound = model.addConstr(p+q <= total_eta*(p+beta*q)/eta,
                                    name=f'dh_chp_energy_limit[{period},{year}]')
            checks.append((p,q,v,gas,weight,carbon_price,power_row,heat_row,bound))
    obj = model.getObjective()
    for name, terms in (('per_mw',power),('boiler_gen_cost',heat),('CO2_emissions_cost',emissions)):
        delta = gp.quicksum(terms)
        ctx[name] += delta
        obj += delta
    model.setObjective(obj)
    model.update()
    for p,q,v,gas,weight,price,power_row,heat_row,bound in checks:
        if (not close(p.Obj,weight*(gas+price*carbon)/eta) or not close(q.Obj,weight*gas*beta/eta)
                or not close(v.Obj,weight*gas/boiler_eta) or not close(model.getCoeff(power_row,p),carbon/eta)
                or not close(model.getCoeff(heat_row,q),carbon*beta/eta)
                or not close(model.getCoeff(heat_row,v),carbon/boiler_eta)
                or bound.Sense != '<' or bound.RHS != 0.
                or not close(model.getCoeff(bound,p),1.-total_eta/eta)
                or not close(model.getCoeff(bound,q),1.-total_eta*beta/eta)):
            raise ValueError('Built district fuel objective, emissions or energy limit differs')
    return dict(hourly_fuel_rows=len(checks), effective_chp_efficiency=eta, boiler_efficiency=boiler_eta,
                total_efficiency_ceiling=total_eta, carbon_price_applies_to_power_terms=True)


def audit_district_heat_solution(ctx, policy):
    if ctx['ucp'].Status != 2:
        raise ValueError('Expected an optimal district heat dispatch')
    report = {}
    fuel = policy['fuel']
    for year in ctx['years']:
        previous = start = policy['storage_mwh']/2.
        peak = residual = 0.
        delivered = []
        gas_in = []
        for period in PERIODS:
            key = SITE,period,year
            stock, chp, demand, missing = (ctx[name][key].X for name in (
                'heat_sto_level','heat_input','heat_gen','noload_heat_dh'))
            produced = fsum([chp, *(ctx['heat_pump_dh'][(u,*key)].X for u in ctx['HeatPump_units']),
                             *(ctx['boiler_dh_generation'][(u,*key)].X for u in ctx['thermal_units'])])
            shift = stock-previous
            electricity = ctx['generation'][key].X
            chp_gas = (electricity+fuel['heat_extraction_coefficient']*chp)/fuel['chp_effective_efficiency']
            boiler_gas = ctx['boiler_dh_generation'][BOILER,SITE,period,year].X/fuel['boiler_efficiency']
            if (not -TOL <= stock <= policy['storage_mwh']+TOL or not close(missing,0.)
                    or abs(shift) > policy['storage_power_mw']+TOL or not close(shift,produced-demand)
                    or not close(demand,ctx['heat_demand'][key])
                    or electricity+chp > fuel['maximum_total_efficiency']*chp_gas+TOL):
                raise ValueError(f'Invalid district heat dispatch at {key}')
            peak = max(peak,abs(shift))
            residual = max(residual,abs(shift-produced+demand))
            previous = stock
            delivered.append(demand)
            gas_in.append(chp_gas+boiler_gas)
        if not close(previous,start) or any(not close(ctx['heat_pump_sto_soc'][u,PERIODS[-1],year].X,0.)
                                               for u in ctx['HeatPump_units']):
            raise ValueError(f'Year-end heat stock is not cyclic in {year}')
        report[year] = dict(delivered_heat_mwh=fsum(delivered), maximum_stock_change_mwh=peak,
                            maximum_heat_balance_residual_mwh=residual, gas_input_mwh=fsum(gas_in),
                            direct_site_co2_t=fuel['gas_co2_t_per_mwh_fuel']*fsum(gas_in))
    return report

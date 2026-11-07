"""Bind the future national exchange envelope and the explicit price policy."""
import json
from math import isfinite
from pathlib import Path

from electricity_project_integration import digest_file
from multisector_eraa_exchange import (
    HOURS, TOL, YEARS, audit_solved_country_exchange, install_country_exchange,
    read_multisector_envelope, render_future_exchange,
)


DATA = Path('data/planning_exchange')
CSV = DATA/'candidate_hourly_mw.csv'
MANIFEST = DATA/'preflight.json'
PRICE = DATA/'price_policy.json'
PRICE_MODE = 'symmetric_native_model_units'


def load_country_exchange_inputs(project, frozen):
    root = project.resolve()

    def read(relative):
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or not path.is_file() or digest_file(path) != frozen.get(relative.as_posix()):
            raise ValueError('Future exchange input is not frozen: '+str(relative))
        return path.read_bytes()

    hourly = read_multisector_envelope(read(CSV),read(MANIFEST))
    price = json.loads(read(PRICE))
    if (price.get('status') != 'approved_scenario_assumption' or price.get('mode') != PRICE_MODE
            or not price.get('source') or not price.get('interpretation')
            or set(price['discounted_signed_coefficient_by_year']) != set(map(str,YEARS))
            or not all(isfinite(price[key]) for key in ('base_price_model_units_per_mwh','annual_increase_percent'))
            or price['annual_increase_percent'] <= -100):
        raise ValueError('The signed purchase/export price is not explicitly approved')
    return hourly,price


def audit_signed_price(ctx, price):
    model = ctx['ucp']
    model.update()
    if (abs(ctx['Import_Price']-price['base_price_model_units_per_mwh']) > TOL
            or abs(ctx['Imp_price_inc']-price['annual_increase_percent']) > TOL):
        raise ValueError('Native price or escalation differs from the declared scenario')
    accepted = {}
    for year in ctx['years']:
        expected = price['discounted_signed_coefficient_by_year'][str(year)]
        native = ctx['NPV'][year]*ctx['Import_Price']*(1+ctx['Imp_price_inc']/100)**(year-ctx['hor_yrs'][0])
        if not all(isfinite(x) for x in (expected,native)) or abs(expected-native)>TOL:
            raise ValueError(f'Wrong discounted national trade price for {year}')
        for hour in HOURS:
            coefficient = ctx['imports'][hour,year].Obj
            if not isfinite(coefficient) or abs(coefficient-expected)>TOL:
                raise ValueError(f'Wrong built signed trade coefficient for {year}/{hour}')
        accepted[str(year)] = expected
    return dict(mode=PRICE_MODE,discounted_coefficient=accepted,source=price['source'])


def install_and_audit_country_exchange(ctx, hourly, price):
    model = ctx['ucp']
    if ctx.get('_future_country_exchange_model') is model or ctx.get('FUTURE_SIGNED_TRADE') is not True:
        raise ValueError('Expected the signed native source and a fresh national exchange window')
    prepared = install_country_exchange(ctx,{year:hourly[year] for year in ctx['years']})
    for (hour,year),flow,krsko,old,gross_in,gross_out,net_in,net_out,slovenia in prepared:
        if (flow.LB != -gross_out or flow.UB != gross_in or old.RHS != gross_in):
            raise ValueError(f'Built gross national exchange differs at {year}/{hour}')
        for name,coeffs,rhs,sense in (
            ('hr_gross_krsko_in',{krsko.index:1.},slovenia,'<'),
            ('hr_net_power_in',{flow.index:1.,krsko.index:1.},net_in,'<'),
            ('hr_net_power_out',{flow.index:1.,krsko.index:1.},-net_out,'>'),
        ):
            row = model.getConstrByName(f'{name}[{hour},{year}]')
            terms = model.getRow(row)
            actual = {terms.getVar(i).index:terms.getCoeff(i) for i in range(terms.size())}
            if row.Sense != sense or row.RHS != rhs or actual != coeffs:
                raise ValueError(f'Built Krško/national flow row differs at {year}/{hour}')
    ctx['_future_country_exchange'] = prepared
    ctx['_future_country_exchange_model'] = model
    return dict(hours=len(prepared),price=audit_signed_price(ctx,price),
                krsko_included_in_physical_net_import=True)


def audit_solved_exchange(ctx):
    if ctx['ucp'].Status != 2:
        raise ValueError('Expected an optimal native solution before exchange audit')
    return audit_solved_country_exchange(ctx['_future_country_exchange'])


def render_country_build(source):
    """Use only for the signed national build, with the runtime envelope installed."""
    anchor = 'import_cost        = gp.quicksum('
    if source.count(anchor) != 1 or 'FUTURE_SIGNED_TRADE' in source:
        raise ValueError('Unexpected native signed price objective anchor')
    code = render_future_exchange(source).replace(anchor,'FUTURE_SIGNED_TRADE = True\n'+anchor)
    compile(code,'<isolated multisector signed exchange build>','exec')
    return code

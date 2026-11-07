"""Check the resolved electricity-only model before a final portfolio solve."""
from itertools import product
from math import isfinite

from electricity_krsko_preflight import audit_built_limits, near


HOURS = tuple(range(1, 8761))


def render_signed_outside_prices(source):
    """Preserve the two tariff levels while keeping the buy price above sell.

    This renders the isolated electricity-only native objective. The
    hourly multiplier can be negative, which reverses the raw ordering.
    """
    for direction, order in (('export', 'min'), ('import', 'max')):
        prefix = f'{direction}_cost_year = {{'
        if source.count(prefix) != 1:
            raise ValueError(f'Unexpected {direction} objective block')
        start = source.index(prefix)
        end = source.index('\n}', start)
        block = source[start:end]
        sell = 'Export_Price_policy_df_distr_year[System, zone, year]'
        buy = 'Import_Price_policy_df_distr_year[System, zone, year]'
        price = sell if direction == 'export' else buy
        factor = 'el_hourly_cost[System, period]'
        original = f'* {price}\n        * {factor}'
        if block.count(original) != 1:
            raise ValueError(f'Unexpected {direction} tariff expression')
        updated = block.replace(original, f'* {order}({buy} * {factor}, {sell} * {factor})')
        source = source[:start] + updated + source[end:]
    return source


def audit_planning_window(ctx, model, zones, limits_mw, operating_years):
    """Compare the native model with a separately frozen planning specification.

    Zones are (zone, system). Limits are keyed by (generator, zone, system,
    year) and include fixed-zero options. Supply these from the approved
    input manifest, independently of the mapped model globals.
    """
    years = tuple(ctx['years'])
    zones = set(zones)
    if (not years or len(set(years)) != len(years)
            or not zones or set(ctx['active_zones_system_df_list']) != zones
            or any(ctx[flag] for flag in ('model_heating', 'model_cooling',
                                        'model_transport_and_industry', 'model_efuel'))):
        raise ValueError('Unexpected electricity-only planning scope')
    weights = ctx.get('representative_period_weights') or {}
    if (tuple(ctx['periods']) != HOURS or not set(weights) <= set(HOURS)
            or any(not near(weight, 1.) for weight in weights.values())
            or not near(ctx['ANNUAL_REPRESENTED_HOURS'], 8760.)):
        raise ValueError('Expected all 8,760 model hours at unit weight')
    if (not isfinite(operating_years) or operating_years <= 0
            or not near(ctx['multip'], operating_years)):
        raise ValueError('Unexpected operating-cost horizon multiplier')
    balances = ctx['energy_balance']
    expected_balances = {(zone, system, p, y) for (zone, system), p, y in
                         product(zones, HOURS, years)}
    if balances.keys() != expected_balances:
        raise ValueError('Built hourly electricity balances are incomplete')

    resolved = ctx['cap_inv_lim_long_lim']
    rows = ctx['max_inv_unit']
    variables = ctx['cap_inv']
    expected_units = {(*unit, y) for unit, y in
                      product(ctx['capacity_generator'], years)}
    if limits_mw.keys() != expected_units or rows.keys() != expected_units:
        raise ValueError('Built investment limits differ in scope from the manifest')
    model.update()
    for key, limit in limits_mw.items():
        unit, zone, _, year = key
        row = rows[key]
        terms = model.getRow(row)
        if (not isfinite(limit) or limit < 0
                or not near(resolved[unit, year, zone], limit)
                or row.Sense != '<' or not near(row.RHS, limit)
                or terms.size() != 1 or not near(terms.getCoeff(0), 1.)
                or terms.getVar(0).index != variables[key].index):
            raise ValueError(f'Wrong built investment limit for {key}')
    audit_built_limits(ctx, model, years)

    imports, exports = ctx['import_outside'], ctx['export_outside']
    if imports.keys() != exports.keys():
        raise ValueError('Outside exchange directions differ in scope')
    for key, buy in imports.items():
        sell = exports[key]
        if not all(isfinite(v) for v in (buy.Obj, sell.Obj)) or buy.Obj + sell.Obj < -1e-12:
            raise ValueError(f'Profitable simultaneous external import and export for {key}')

"""Observational channel checks and exports; no optimization equations."""
from pathlib import Path
import ast
import csv
import gzip
import json
import math
import numpy as np
from common import R, OLD, YEARS, DONORS, load, save, save_pickle, sha, quality


class ReferenceDone(BaseException):
    pass


FAMILIES = ['cap_inv', 'cap_inv_FC', 'heat_pump_dh_cap', 'heat_pump_ind_cap',
            'trad_boiler_ind_cap_inv', 'trad_boiler_dh_cap_inv', 'stat_sto_cap_inv',
            'h2_sto_max_cap', 'h2_electrolysis_max_cap', 'extra_cap_inv']


def profile_key(case):
    return 'avg_yield__normal' if case == 'average' else case + '__channel'


def input_signatures(ctx, digest):
    tree = ast.parse((R / 'sources/Build_model_RH.py').read_text(encoding='utf8'))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
             and isinstance(n.ctx, ast.Load)}
    excluded = {'heat_demand', 'cooling_demand', 'HP_COP_period', 'aval_factor_plant',
                'year', 'unit', 'period', 'index', 'genco', 'i', 'j', 'p', 'y'}
    hashes = {}
    for name in sorted(names):
        if name in excluded or name.endswith('_cumulative') or name.startswith('values_'):
            continue
        value = ctx.get(name)
        if isinstance(value, (bool, int, float, str, list, tuple, dict)):
            try:
                hashes[name] = digest(value)
            except (TypeError, ValueError):
                continue
    assert all(n in hashes for n in ('NPV', 'periods', 'years', 'max_load', 'cap_inv_cost'))
    return hashes


def prebuild(ctx, ns):
    year = int(ctx['years'][0])
    out = ns['JOB'] / str(year)
    out.mkdir(parents=True, exist_ok=True)
    assert not (out / 'accepted.json').exists()
    files = [p for p in out.iterdir() if p.is_file()]
    if files:
        history = out / 'incomplete_attempts'
        history.mkdir(exist_ok=True)
        dest = history / str(len(list(history.iterdir())) + 1)
        dest.mkdir()
        for p in files:
            p.rename(dest / p.name)
    assert ctx['ai_climate_available'] and ctx['temperature_coupling_available']
    assert ctx['profile_channel_overrides'] == {}
    assert list(ctx['periods']) == list(range(1, 8761)) and ctx['window'] == 2
    case = ns['case']
    digest = ns['digest']
    signatures = input_signatures(ctx, digest)
    result = {'case': case, 'year': year, 'window_years': ctx['years'],
              'common_input_hashes': signatures, 'thermal_hashes': {}, 'mapping': []}
    if not ns.get('reference_mode'):
        ref = load(R / 'reference' / str(year) / 'prebuild_validation.json')
        assert signatures == ref['common_input_hashes'], 'Non-climate model input mismatch'
    expected_heat = dict(ctx['baseline_heat_demand'])
    expected_cooling = dict(ctx['baseline_cooling_demand'])
    expected_cop = dict(ctx['baseline_HP_COP_period'])
    from scripts import scenario_manager as manager
    for yy in ctx['years']:
        record = ctx['ai_scenario_cache'][yy][profile_key(case)]
        profile = record['profile']
        assert np.array_equal(profile, manager._channel_profile(case, yy))
        units, diag = ctx['decompose_to_h2res_units'](
            profile, country_code=ctx['country_code'],
            wind_resolution_mode=ctx['wind_resolution_mode'], return_diagnostics=True)
        errors = {}
        for channel, weights in [('solar', manager._get_solar_units('HR')),
                                 ('wind', manager._get_wind_units('HR'))]:
            aggregate = sum(units[u] * float(w) for u, w in weights.items()) / sum(weights.values())
            error = float(np.max(np.abs(aggregate - profile[:, 0 if channel == 'solar' else 1])))
            assert error <= 1e-12, (case, yy, channel, 'weighted mapping error', error)
            errors[channel] = error
        for unit, values in units.items():
            assert np.isfinite(values).all() and np.min(values) >= 0 and np.max(values) <= 1
            executed = np.array([ctx['aval_factor_plant'][unit, p, yy] for p in ctx['periods']])
            assert np.array_equal(executed, np.asarray(values, dtype=float)), (unit, yy)
        result['mapping'].append({'year': yy, 'max_errors': errors,
                                  'input_profile_sha256': __import__('hashlib').sha256(profile.tobytes()).hexdigest()})
        ctx['apply_temperature_coupling_to_runtime'](
            profile[:, 2], year=yy, country_code=ctx['country_code'], periods=ctx['periods'],
            heat_demand=expected_heat, cooling_demand=expected_cooling, hp_cop_period=expected_cop,
            baseline_heat_demand=ctx['baseline_heat_demand'],
            baseline_cooling_demand=ctx['baseline_cooling_demand'],
            baseline_hp_cop_period=ctx['baseline_HP_COP_period'],
            district_heat_units=ctx['temperature_district_heat_units'],
            general_heat_units=ctx['temperature_general_heat_units'], cooling_units=ctx['temperature_cooling_units'],
            atw_cop_units=ctx['temperature_atw_cop_units'], heating_base_c=ctx['temperature_heating_base_c'],
            cooling_base_c=ctx['temperature_cooling_base_c'], demand_shape_blend=ctx['temperature_demand_shape_blend'],
            scale_annual_totals=ctx['temperature_scale_annual_totals'],
            district_heat_elasticity=ctx['temperature_district_heat_elasticity'],
            general_heat_elasticity=ctx['temperature_general_heat_elasticity'],
            cooling_elasticity=ctx['temperature_cooling_elasticity'],
            cop_temperature_sensitivity=ctx['temperature_hp_cop_sensitivity'])
    for name, expected in [('heat_demand', expected_heat), ('cooling_demand', expected_cooling),
                           ('HP_COP_period', expected_cop)]:
        actual = {k: v for k, v in ctx[name].items() if k[-1] in ctx['years']}
        expected = {k: v for k, v in expected.items() if k[-1] in ctx['years']}
        assert digest(actual) == digest(expected), 'Executed thermal input mismatch: ' + name
        result['thermal_hashes'][name] = digest(actual)
        if not ns.get('reference_mode') and not case.startswith('temperature_'):
            assert digest(actual) == ref['thermal_hashes'][name], 'Unchanged temperature channel changed'
    weather_units = set(units)
    other = {k: v for k, v in ctx['aval_factor_plant'].items()
             if k[-1] in ctx['years'] and k[0] not in weather_units}
    result['other_availability_sha256'] = digest(other)
    if not ns.get('reference_mode'):
        assert result['other_availability_sha256'] == ref['other_availability_sha256']
    state = ns['frozen_state'](ctx)
    result['inherited_state_sha256'] = digest(state)
    if year == 2020:
        assert digest(state) == digest(ns['snapshot']['state'])
    save_pickle(out / f'inherited_{year}.pkl', {'state': state, 'hor_yrs': ctx['hor_yrs'],
                'years': ctx['years'], 'index': ctx['i'], 'case': case, 'fence': ns['fence']})
    save(out / 'prebuild_validation.json', result)


def terms(ctx, model, out, values=None):
    rows = []
    seen = set()
    for family in FAMILIES:
        for key, var in ctx.get(family, {}).items():
            assert var.index not in seen, 'Duplicate capacity variable'
            seen.add(var.index)
            key = key if isinstance(key, tuple) else (key,)
            value = float(values[var.VarName]) if values is not None else float(var.X)
            rows.append({'family': family, 'key_json': json.dumps(key, default=lambda v: v.item()),
                         'variable': var.VarName, 'value': value, 'objective_coefficient': var.Obj,
                         'objective_contribution': var.Obj * value, 'decision_year': int(key[-1]),
                         'window_start': int(ctx['years'][0]), 'realized': int(key[-1] == ctx['years'][0]),
                         'unit': 'MWh' if family in ('stat_sto_cap_inv', 'h2_sto_max_cap') else 'MW',
                         'capacity_addition': int(family != 'extra_cap_inv'),
                         'NPV': float(ctx['NPV'][key[-1]])})
    with (out / 'investment_terms.csv').open('w', encoding='utf8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def before(model, ctx, ns):
    model.update()
    year = int(ctx['years'][0])
    out = ns['JOB'] / str(year)
    ns.update(OUT=out, year=year)
    audit = {'case': ns['case'], 'job': ns['job'], 'year': year,
             'window_years': ctx['years'], 'fence': ns['fence'],
             'inherited_state_sha256': ns['digest'](ns['frozen_state'](ctx)),
             'model_fingerprint': int(model.Fingerprint), 'hashes': {},
             'NPV': {str(y): float(ctx['NPV'][y]) for y in ctx['years']}}
    for name in ('heat_demand', 'cooling_demand', 'HP_COP_period', 'aval_factor_plant'):
        audit['hashes'][name] = ns['digest']({k: v for k, v in ctx[name].items()
                                           if isinstance(k, tuple) and k[-1] in ctx['years']})
    save(out / 'input_audit.json', audit)
    with gzip.open(out / 'runtime_accounting_inputs.csv.gz', 'wt', encoding='utf8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['family', 'key_json', 'value'])
        for name in ('demand', 'heat_demand', 'cooling_demand', 'Industry_HT_demand', 'H2_demand',
                     'ev_Demand', 'HP_COP_period'):
            for key, value in ctx.get(name, {}).items():
                key = key if isinstance(key, tuple) else (key,)
                if key[-1] in ctx['years']:
                    writer.writerow([name, json.dumps(key, default=lambda v: v.item()), float(value)])
    if not ns.get('reference_mode'):
        return
    old = OLD / 'multisector/jobs/avg_path' / str(year)
    marker = load(old / 'accepted.json')
    for name, expected in marker['files'].items():
        assert sha(old / name) == expected, 'Corrupt reference record: ' + name
    previous = load(old / 'input_audit.json')
    result = load(old / 'solver_result.json')
    assert quality(result)
    identical = (audit['hashes'] == previous['hashes']
                 and audit['inherited_state_sha256'] == previous['inherited_state_sha256']
                 and audit['NPV'] == previous['NPV']
                 and int(model.Fingerprint) == int(result['fingerprint']))
    report = {'year': year, 'reuse_proved': identical, 'old_marker_sha256': sha(old / 'accepted.json'),
              'old_input_sha256': sha(old / 'input_audit.json'), 'old_objective': result['objective'],
              'model_fingerprint': int(model.Fingerprint), 'input_and_state_equal': identical,
              'new_solve_started': False, 'old_source': str(old)}
    if identical:
        variables = model.getVars()
        names = {v.VarName for v in variables}
        assert len(names) == len(variables), 'Ambiguous native variable names'
        wanted = {v.VarName for name in FAMILIES for v in ctx.get(name, {}).values()}
        selected = {}
        contributions = []
        total = 0.
        with gzip.open(old / 'all_variables.csv.gz', 'rt', encoding='utf8') as f:
            for row in csv.DictReader(f):
                name = row['variable']
                assert name in names
                names.remove(name)
                value = float(row['value'])
                variable = model.getVarByName(name)
                contributions.append(float(variable.Obj) * value)
                if len(contributions) == 50000:
                    total = math.fsum([total, math.fsum(contributions)])
                    contributions.clear()
                if name in wanted:
                    selected[name] = value
        assert not names and set(selected) == wanted
        reconstructed = math.fsum([total, math.fsum(contributions), model.getObjective().getConstant()])
        report['objective_reconstruction_gap'] = reconstructed - result['objective']
        assert abs(report['objective_reconstruction_gap']) < .01
        terms(ctx, model, out, selected)
        report['investment_terms_sha256'] = sha(out / 'investment_terms.csv')
    save(out / 'reference_audit.json', report)
    raise ReferenceDone()

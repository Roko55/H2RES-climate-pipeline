"""Render isolated electricity sources and gate the actual pre-solve model."""
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path

from electricity_exchange_preflight import audit_stress_exchange
from electricity_window_preflight import audit_planning_window, render_signed_outside_prices


SET_OBJECTIVE = 'ucp.setObjective((total_cost)/1,GRB.MINIMIZE)'
ROKO = Path('E:/Roko/remote_access')
NATIVE_INPUTS = ('flex_tech_dat', 'genco_dat', 'policy_dat', 'el_hourly_cost_dat',
                 'fuel_price_dat', 'demand_dat', 'demand_industry_el', 'demand_heating_el',
                 'inflows_dat', 'avl_factor_plant_dat')
PREPARED_INPUTS = ('H2RES_CLIMATE_PROFILE_DAT', 'H2RES_CLIMATE_SUMMARY_DAT')
LEGACY_MAPPING = 'data/genco_data_HR_sdewes18.csv'


def digest_file(path):
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def render_planning_objective(source):
    """Insert the audit after objective coefficients are set, before any solve."""
    if source.count(SET_OBJECTIVE) != 1 or 'audit_bound_planning_model' in source:
        raise ValueError('Unexpected objective entry point')
    start = source.index(SET_OBJECTIVE)
    if 'ucp.optimize(' in source[:start]:
        raise ValueError('A native optimization precedes the objective gate')
    updated = render_signed_outside_prices(source).replace(
        SET_OBJECTIVE, SET_OBJECTIVE+'\nfrom electricity_project_integration import audit_bound_planning_model\n'
        'audit_bound_planning_model(globals(), ucp)')
    compile(updated, '<isolated electricity objective>', 'exec')
    return updated


class ElectricityDryBuildFinished(BaseException):
    """Expected stop before the native optimizer can be entered."""


def render_electricity_dry_build(source):
    code = render_planning_objective(source)
    hook = 'audit_bound_planning_model(globals(), ucp)'
    if code.count(hook) != 1:
        raise ValueError('Unexpected electricity dry-build anchor')
    code = code.replace(hook,hook+'\nfrom electricity_project_integration import stop_after_electricity_matrix\n'
                        'stop_after_electricity_matrix(globals(), ucp)')
    compile(code,'<isolated electricity dry build>','exec')
    return code


def stop_after_electricity_matrix(ctx, model):
    project = Path.cwd().resolve()
    destination = (project/'dry_build/matrix_report.json').resolve()
    if os.name != 'nt' or ROKO.resolve() not in project.parents or project not in destination.parents:
        raise ValueError('Use a separate owned Roko electricity dry-build directory')
    model.update()
    if model.SolCount != 0:
        raise ValueError('Electricity matrix already has a solution')
    destination.parent.mkdir(exist_ok=True)
    with destination.open('x',encoding='utf-8') as report:
        json.dump(dict(status='matrix_built_without_optimization',solver_invocations=0,
                       planning_approved=False,years=list(ctx['years']),hours=len(ctx['periods']),
                       rows=model.NumConstrs,columns=model.NumVars,fingerprint=model.Fingerprint,
                       exchange_2030=ctx.get('eraa_2030_build_audit'),
                       manifest_sha256=os.environ['H2RES_ELECTRICITY_PLANNING_MANIFEST_SHA256']),report)
    raise ElectricityDryBuildFinished()


def render_stress_boundary(source, fraction):
    """Derate primary and outside Croatian electric paths only."""
    if fraction not in (0., .5, 1.) or isinstance(fraction, bool):
        raise ValueError('Unsupported exchange fraction')
    prefix = 'max_transfer_systexpimp_pos = ucp.addConstrs('
    if source.count(prefix) != 1 or 'audit_stress_exchange' in source or 'ucp.optimize(' in source:
        raise ValueError('Unexpected stress build entry point')
    scope = ('if include_secc_links or model_efuel or model_H2_transmission:\n'
             '    raise ValueError("Unsupported exchange scope for this stress overlay")\n')
    source = source.replace(prefix, scope+prefix)
    for name in ('max_transfer_systexpimp_pos', 'max_transfer_systexpimp_neg'):
        start = source.index(name+' = ucp.addConstrs(')
        end = source.index('name="'+name+'"', start)
        block = source[start:end]
        old = '<= transfer_cap_hour_prim['
        if block.count(old) != 1:
            raise ValueError('Unexpected primary exchange row: '+name)
        factor = (f'({float(fraction)!r} if genco in transmission_units_el '
                  'and ((System == "HR") != (ToFrom == "HR")) else 1.0)')
        source = source[:start]+block.replace(old,'<= '+factor+' * transfer_cap_hour_prim[')+source[end:]
    old = '<= transfer_cap_outside[genco, year, zone, System]\n'
    if source.count(old) != 2:
        raise ValueError('Unexpected number of outside electricity directions')
    source = source.replace(old, f'<= ({float(fraction)!r} if System == "HR" else 1.0) * '+old[3:])
    source += ('\nfrom electricity_exchange_preflight import audit_stress_exchange\n'
               f'audit_stress_exchange(globals(), ucp, {float(fraction)!r})\n')
    compile(source, '<isolated electricity stress build>', 'exec')
    return source


def validate_planning_manifest(raw, expected_sha256, project):
    """Require a frozen independent contract; never derive targets from the build."""
    if sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Electricity planning manifest hash mismatch')
    spec = json.loads(raw)
    if set(spec) != {'schema', 'project', 'scope', 'years', 'zones', 'limits_mw',
                     'operating_years', 'inputs_sha256'}:
        raise ValueError('Unexpected electricity planning manifest fields')
    if (spec['schema'] != 1 or spec['scope'] != 'electricity_only'
            or Path(spec['project']).resolve() != project or not spec['inputs_sha256']):
        raise ValueError('Electricity planning manifest has the wrong project or scope')
    years, zones, weight = spec['years'], spec['zones'], spec['operating_years']
    if (not years or any(type(year) is not int for year in years) or len(set(years)) != len(years)
            or not zones or any(not isinstance(zone, list) or len(zone) != 2
                                or not all(isinstance(item, str) and item for item in zone) for zone in zones)
            or len({tuple(zone) for zone in zones}) != len(zones)
            or type(weight) not in (int, float) or not isfinite(weight) or weight <= 0):
        raise ValueError('Invalid planning years, zones or horizon weight')
    limits = {}
    for item in spec['limits_mw']:
        if not isinstance(item, list) or len(item) != 5:
            raise ValueError('Invalid investment row in manifest')
        unit, zone, system, year, value = item
        key = (unit, zone, system, year)
        if (not isinstance(unit, str) or not unit or [zone, system] not in zones or year not in years
                or type(value) not in (int, float) or not isfinite(value) or value < 0 or key in limits):
            raise ValueError('Invalid or duplicate investment row in manifest')
        limits[key] = value
    if not limits or not spec['years'] or not spec['zones']:
        raise ValueError('Empty approved planning scope')
    checked = set()
    for rel, digest in spec['inputs_sha256'].items():
        path = (project/rel).resolve()
        if Path(rel).is_absolute() or path in checked or not path.is_relative_to(project) or not path.is_file():
            raise ValueError('Manifest input leaves the isolated project: '+rel)
        if digest_file(path) != digest:
            raise ValueError('Frozen electricity input hash mismatch: '+rel)
        checked.add(path)
    return spec, limits


def audit_active_input_paths(ctx, env, project, spec):
    frozen = {(project/rel).resolve() for rel in spec['inputs_sha256']}
    for field in NATIVE_INPUTS:
        if not ctx.get(field) or (project/ctx[field]).resolve() not in frozen:
            raise ValueError('Active electricity input is not frozen: '+field)
    if not getattr(ctx.get('last_climate_apply_result'), 'prepared_inputs', False):
        raise ValueError('Expected the frozen prepared climate pathway, not runtime generation')
    for field in PREPARED_INPUTS:
        if not env.get(field) or (project/env[field]).resolve() not in frozen:
            raise ValueError('Active climate input is not frozen: '+field)
    if (project/LEGACY_MAPPING).resolve() not in frozen:
        raise ValueError('Legacy inventory used to map investment rows is not frozen')


def audit_bound_planning_model(ctx, model):
    project = Path.cwd().resolve()
    root = ROKO.resolve()
    if os.name != 'nt' or root not in project.parents:
        raise ValueError('Planning optimization is limited to an isolated Roko project')
    manifest = Path(os.environ['H2RES_ELECTRICITY_PLANNING_MANIFEST']).resolve()
    if project not in manifest.parents:
        raise ValueError('Planning manifest must be inside the isolated project')
    spec, limits = validate_planning_manifest(
        manifest.read_bytes(), os.environ['H2RES_ELECTRICITY_PLANNING_MANIFEST_SHA256'], project)
    if tuple(ctx['years']) != tuple(spec['years']):
        raise ValueError('Planning years differ from the independent manifest')
    audit_active_input_paths(ctx, os.environ, project, spec)
    audit_planning_window(ctx, model, map(tuple, spec['zones']), limits, spec['operating_years'])

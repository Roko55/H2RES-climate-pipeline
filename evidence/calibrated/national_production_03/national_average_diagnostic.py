"""Isolated national Average matrix inspection; no solver optimization or checkpoint."""
import ast
import csv
from datetime import datetime, timezone
import json
from math import fsum, isfinite
import os
from pathlib import Path
import shutil
import socket
import sys
import traceback

from electricity_project_integration import digest_file
from multisector_exchange_integration import load_country_exchange_inputs, install_and_audit_country_exchange, render_country_build
from multisector_fixed_demand import (
    load_schedules, apply_heat_meter_split, verify_heat_meter_input, install_inputs, verify_and_fix_built_demand,
)
from multisector_hydro_integration import load_hydro_inputs, prepare_hydro_window, install_and_audit_hydro, render_unique_hydro_capex
from multisector_hydrogen_scope import prepare_hydrogen_scope, audit_built_hydrogen_scope
from multisector_district_heat import (
    load_district_heat_policy, prepare_district_heat, install_district_heat_rows, install_district_fuel,
)
from multisector_stationary_storage import load_storage_policy, install_storage_retirement, install_storage_limits
from multisector_power_policy import audit_native_power_policy, load_gas_stock
from multisector_dispatchable_renewables import (
    POLICY as BIO_POLICY, apply_dispatchable_renewables, audit_and_limit_dispatchable_renewables,
    load_dispatchable_renewables, render_dispatchable_renewables_reader,
)
from audit_production_families import audit_built_supply, supply_inventory
from prepare_spatial_training_contract import render_capacity_aware_cache_keys


ROOT = Path(__file__).resolve().parent
REMOTE = Path('E:/Roko/remote_access')
DONOR = REMOTE/'channel_sensitivity_20261102'
SEED = REMOTE/'hr_2020_calibration_20261105/native_pilot_r6bjz/run_01'
STOCK = Path('data/SC/genco_data_HR_sdewes.csv')
STOCK_HASH = 'e04ca9be6ebbcb283d15708584dae0457ca64e7db7736c343e70743aa96fd320'
YEARS = (2025, 2030)
HOURS = tuple(range(1, 8761))
ASSUMPTIONS = (
    'National R6bjz is a physical starting point only; its conditioned dispatch is not transferred.',
    '2025 year-end regular wind nameplate and PV AC connection capacities are held through 2030 before new model investments. Testing wind is excluded.',
    'Generator cost and new renewable limits reuse the native stationary Average assumptions where a matching renewable exists.',
    'Thermal and other non-renewable residual proxies use conditional historical engineering inputs pending independent future reconciliation.',
    'Gross meter candidate: subtract embedded electric heat and pumping before adding explicit native demand. Pumping inclusion is a scenario assumption.',
    'Future heat demand is scaled from the calibrated base. The Zagreb technical limits and CHP fuel envelope are diagnostic assumptions.',
    'Keep the V6 carbon price without absolute carbon or renewable share constraints for the common weather sensitivity.',
)


class MatrixBuilt(BaseException):
    pass


def json_safe(value):
    if isinstance(value, dict):
        return {str(key):json_safe(item) for key,item in value.items()}
    if isinstance(value, (tuple,list)):
        return [json_safe(item) for item in value]
    if hasattr(value,'item') and getattr(value,'ndim',None) == 0:
        return json_safe(value.item())
    return value


def save(path, value):
    tmp = path.with_suffix('.pending')
    tmp.write_text(json.dumps(json_safe(value),indent=2,sort_keys=True,allow_nan=False),encoding='utf8')
    tmp.replace(path)


def read_json(path):
    return json.loads(path.read_bytes())


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_rows(path, records):
    with path.open('w', encoding='utf8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def inject_before_optimization(source):
    tree = ast.parse(source)
    names = {'optimize', 'optimizeAsync', 'optimizeBatch'}
    all_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr in names]
    direct = [(i, n.value) for i, n in enumerate(tree.body)
              if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
              and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'optimize'
              and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'ucp']
    if len(direct) != 1 or all_calls != [direct[0][1]]:
        raise ValueError('Unexpected native optimizer entry point')
    index = direct[0][0]
    tree.body = tree.body[:index] + ast.parse('_diagnose_matrix(globals())').body
    return ast.unparse(ast.fix_missing_locations(tree)) + '\n'


def render_stationary_manager(source):
    for name in ('solar_spatial','wind_spatial'):
        source = source.replace(f"cache_key = ('{name}',", f'cache_key = ("{name}",')
    return render_capacity_aware_cache_keys(source)


def stock_inputs(seed_rows, native_rows, exits, solar, wind):
    fieldnames = set(native_rows[0])
    if set(seed_rows[0]) != fieldnames or set(exits[0]) != fieldnames:
        raise ValueError('Generator CSV headers do not reconcile')
    def unique(records):
        indexed = {r['unit_name']: r.copy() for r in records}
        if len(indexed) != len(records):
            raise ValueError('Duplicate generator row')
        return indexed

    historical, native, retirement = map(unique, (seed_rows, native_rows, exits))
    expected = {'TEPlomin2': 215., 'TERijeka': 343.5, 'HRGasOther2020': 320.1, 'NEKrskoHR': 348.}
    if any(abs(float(historical[u]['cap_mw'])-mw) > 1e-7 for u, mw in expected.items()):
        raise ValueError('Dated exit candidate no longer matches its starting stock')
    for old, pair in (
        ('TEPlomin2', ('TEPlomin2','HRCoalIndustrialProxy')),
        ('TERijeka', ('TERijeka','HROilIndustrialProxy')),
        ('HRGasOther2020', ('HRGasOther2020','HRJertovec2020')),
        ('NEKrskoHR', ('NEKrskoHR',)),
    ):
        if abs(fsum(float(retirement[u]['cap_mw']) for u in pair)-expected[old]) > 1e-7:
            raise ValueError('Split lost starting physical MW: '+old)
    historical.update(retirement)
    renewables = {r['unit_name']: r['candidate_connection_mw'] for r in solar['candidate_by_zone'].values()}
    renewables.update({'HR_Wind_'+zone: r['installed_mw'] for zone, r in wind['stock_by_zone']['2025']['regular'].items()})
    if set(renewables) != {u for u,r in historical.items() if r['technology'] in ('PHOT','WTON')}:
        raise ValueError('Dated renewable stock does not cover the physical fleet')
    for unit, mw in renewables.items():
        historical[unit].update({k: native[unit][k] for k in
                                ('max_inv_period','cap_inv_cost','decom_start_new','life_time')})
        historical[unit].update(cap_mw=str(mw), decom_start_existing_cap='15', final_life_cap='1.0')
    return list(historical.values()), renewables


def stage(project):
    preparation = read_json(DONOR/'preparation.json')
    prepared = preparation['frozen_files']
    base = DONOR/'projects/average'
    staged = {}
    for source in sorted(base.rglob('*')):
        if not source.is_file() or '__pycache__' in source.parts:
            continue
        rel = source.relative_to(base).as_posix()
        key = 'projects/average/'+rel
        actual = digest_file(source)
        if key in prepared and prepared[key] != actual:
            raise ValueError('Archived Average file has changed: '+rel)
        target = project/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source,target)
        staged[rel] = dict(source=str(source),sha256=actual,original_fence_verified=key in prepared)

    upload = ROOT/'inputs'
    bundle = read_json(upload/'manifest.json')
    for rel, expected in bundle.items():
        if digest_file(upload/rel) != expected:
            raise ValueError('Diagnostic input changed in transit: '+rel)
    climate = read_json(project/'climate_sources.json')
    sc = project/'data/SC'
    for rel, item in climate['artifacts'].items():
        if ((rel.endswith('.parquet') and item.get('last_year',9999) > 2015)
                or digest_file(sc/rel) != item['solver_file_sha256']):
            raise ValueError('Archived spatial source or training boundary differs: '+rel)
    ensemble_sha = preparation['ensemble_sha256']
    if (digest_file(sc/'xgb_synthetic_scenarios.npy') != ensemble_sha
            or prepared['projects/average/data/SC/xgb_synthetic_scenarios.npy'] != ensemble_sha
            or preparation['new_training'] is not False):
        raise ValueError('Frozen scenario ensemble changed')
    solar = read_json(upload/'solar.json')
    solar_training = read_json(upload/'solar_training_evidence.json')
    if (solar['candidate_transfer_sha256'] != bundle['data/SC/HR_solar_transfer_macro_to_units.csv']
            or solar_training['profile_sha256'] != bundle['data/SC/HR_solar_macro_profiles.parquet']
            or solar_training['last_input_utc'] != '2015-12-31T23:00:00+00:00'):
        raise ValueError('Dated solar stock/transfer or training provenance differs')
    seed = SEED/'project/data/SC/genco_pilot_2020.csv'
    if (digest_file(seed) != STOCK_HASH
            or digest_file(SEED/'status.json') != bundle['historical_terminal.json']
            or read_json(upload/'historical_terminal.json')['solver_status'] != 2):
        raise ValueError('R6bjz historical evidence failed readback')
    candidate, renewables = stock_inputs(rows(seed),rows(project/STOCK),rows(upload/'retirement.csv'),
                                        solar,read_json(upload/'wind.json'))
    gas, _ = load_gas_stock(upload,bundle)
    candidate = [gas.pop(r['unit_name'],r) for r in candidate]
    candidate.extend(gas.values())
    if BIO_POLICY in bundle:
        apply_dispatchable_renewables(candidate,load_dispatchable_renewables(upload,bundle))
        reader = project/'scripts/read_process_data.py'
        reader.write_text(render_dispatchable_renewables_reader(reader.read_text(encoding='utf-8-sig')),encoding='utf8')
    write_rows(project/STOCK,candidate)
    transfer = project/'data/SC/HR_wind_transfer_nuts_to_units.csv'
    wind_rows = rows(transfer)
    for row in wind_rows:
        unit = row['unit_name']
        if float(row['weight']) != 1. or unit != 'HR_Wind_'+row['source_zone']:
            raise ValueError('Unexpected wind NUTS3 transfer topology')
        row['source_cap_mw'] = row['unit_cap_mw'] = renewables[unit]
    write_rows(transfer,wind_rows)
    for rel in bundle:
        if rel.startswith('data/'):
            dest = project/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(upload/rel,dest)

    model = project/'scripts/Build_model_RH.py'
    src = render_unique_hydro_capex(render_country_build(model.read_text(encoding='utf-8-sig')))
    model.write_text(inject_before_optimization(src),encoding='utf8')
    manager = project/'scripts/scenario_manager.py'
    manager.write_text(render_stationary_manager(manager.read_text(encoding='utf-8-sig')),encoding='utf8')
    files = {f.relative_to(project).as_posix():digest_file(f) for f in project.rglob('*') if f.is_file()}
    save(ROOT/'matrix_inputs.json',dict(status='diagnostic_only',production_approved=False,years=YEARS,
         historical_seed_sha256=STOCK_HASH,assumptions=ASSUMPTIONS,archived_sources=staged,
         uploaded_inputs=bundle,staged_sha256=files,dated_renewable_stock_mw=renewables,
         diagnostic_source_sha256=digest_file(Path(__file__)),ensemble_sha256=ensemble_sha,
         upstream_spatial_archive_ensemble_sha256=climate['ensemble_sha256']))
    return candidate, files


def run():
    if (socket.gethostname().lower() != 'pcsp-mxl0493vpr' or ROOT.parent != REMOTE
            or not ROOT.name.startswith('national_average_matrix_')):
        raise ValueError('Use only an isolated national matrix folder on the project server')
    from electricity_matrix_2030 import resource_check
    resources = resource_check()
    save(ROOT/'admission.json',resources)
    if not resources['ready']:
        raise ValueError('Shared server admission not available')
    project = ROOT/'project'
    project.mkdir(exist_ok=False)
    candidate, frozen = stage(project)
    water, hydro = load_hydro_inputs(project,frozen)
    hourly, price = load_country_exchange_inputs(project,frozen)
    schedules = load_schedules(project,frozen,'average')
    schedules, heat_meter = apply_heat_meter_split(project,frozen,schedules)
    storage = load_storage_policy(project,frozen)
    district = load_district_heat_policy(project,frozen)
    gas = load_gas_stock(project,frozen)
    dispatchable = load_dispatchable_renewables(project,frozen) if BIO_POLICY in frozen else None
    os.chdir(project)
    sys.path[:0] = [str(project),str(REMOTE/'energy_rebuild_20261019/runtime_packages')]
    import gurobipy as gp

    def forbidden(*_args, **_kwargs):
        raise RuntimeError('Optimization and checkpoints are disabled in this diagnostic')

    class MatrixOnlyModel(gp.Model):
        optimize = optimizeAsync = optimizeBatch = forbidden

    gp.Model = MatrixOnlyModel
    state = {}

    def restore(ctx):
        if tuple(ctx['years']) != YEARS or int(ctx['i']) != 1 or tuple(ctx['periods']) != HOURS:
            raise ValueError('Not the isolated Average 2025/2030 hourly window')
        carry = {k:v for k,v in ctx.items() if isinstance(v,dict) and
                 (k.endswith('_cumulative') or k.startswith('values_'))}
        if not carry or any(float(v) != 0. for values in carry.values() for v in values.values()):
            raise ValueError('Unexpected previous investment stock')
        if not ctx.get('ai_climate_available') or not ctx.get('anchor_profile_summary'):
            raise ValueError('Frozen climate profiles were not loaded')
        if (ctx.get('anchor_decomp_diag') or {}).get('wind_resolution_selected') != 'nuts3':
            raise ValueError('Frozen NUTS3 wind mapping was not applied')
        if not ctx.get('anchor_temp_diag'):
            raise ValueError('Frozen temperature was not coupled to the heat/COP inputs')
        state['input_profile_summary'] = ctx['anchor_profile_summary']
        state['zero_prior_investment_keys'] = {k:len(v) for k,v in carry.items()}
        install_storage_retirement(ctx,storage)
        prepare_hydrogen_scope(ctx,project,frozen)
        verify_heat_meter_input(ctx,project,frozen,heat_meter)
        install_inputs(ctx,schedules)
        prepare_hydro_window(ctx,water)
        prepare_district_heat(ctx,district)
        state['native_policy_flags'] = {k:bool(ctx[k]) for k in ('rps_inv','carbonLimit')}

    def audit_district(ctx):
        report = install_district_heat_rows(ctx,district)
        report['fuel'] = install_district_fuel(ctx,district)
        return report

    def inspect_matrix(ctx):
        m = ctx['ucp']
        m.update()
        if type(m) is not MatrixOnlyModel or m.SolCount != 0:
            raise ValueError('Matrix-only guard is not active')
        inventory = supply_inventory(candidate)
        results = {}
        checks = [
            ('supply',lambda:audit_built_supply(ctx,m,inventory)),
            ('exchange',lambda:install_and_audit_country_exchange(ctx,hourly,price)),
            ('demand',lambda:verify_and_fix_built_demand(ctx,schedules)),
            ('storage',lambda:install_storage_limits(ctx,storage)),
            ('hydro',lambda:install_and_audit_hydro(ctx,hydro)),
            ('hydrogen',lambda:audit_built_hydrogen_scope(ctx)),
            ('district_heat',lambda:audit_district(ctx)),
            ('power_policy',lambda:audit_native_power_policy(ctx,gas)),
        ]
        if dispatchable is not None:
            checks.append(('dispatchable_renewables',lambda:audit_and_limit_dispatchable_renewables(ctx,dispatchable)))
        for name, audit in checks:
            try:
                results[name] = dict(passed=True,detail=audit())
            except (ValueError, KeyError, AttributeError) as e:
                results[name] = dict(passed=False,detail=f'{type(e).__name__}: {e}')
        m.update()
        if not isfinite(float(m.Fingerprint)) or m.SolCount != 0:
            raise ValueError('Invalid matrix fingerprint or unexpected solution')
        save(ROOT/'matrix_report.json',dict(status='diagnostic_matrix_built',planning_approved=False,
             optimizer_calls=0,checkpoint_written=False,rows=m.NumConstrs,columns=m.NumVars,
             fingerprint=m.Fingerprint,years=YEARS,checks=results,assumptions=ASSUMPTIONS,
             input_manifest_sha256=digest_file(ROOT/'matrix_inputs.json'),**state))
        raise MatrixBuilt()

    settings = read_json(project/'review_command.json')['settings']
    for key in tuple(os.environ):
        if key.startswith(('H2RES_', 'LEGACY_', 'FULL_')):
            os.environ.pop(key)
    os.environ.update({key:val for key,val in settings.items() if key in
                      ('H2RES_CLIMATE_COUNTRY','H2RES_CLIMATE_SCENARIO','H2RES_TEMPERATURE_COUPLING')})
    os.environ.update(FULL_START_INDEX='1',FULL_MATCHED_YEAR='2025',H2RES_WIND_RESOLUTION_MODE='nuts3',
                      H2RES_RUN_LABEL='matrix_average',H2RES_WRITE_SOLVER_SUMMARY='0',
                      H2RES_SOLVER_SUMMARY_DIR=str(ROOT),H2RES_CAPACITY_EXPORT_DIR=str(ROOT))
    main = project/'main_rolling_horizon_v3.py'
    namespace = dict(__name__='__main__',__file__=str(main),_full_restore=restore,
                     _full_checkpoint=forbidden,_channel_prebuild=lambda _ctx:None,_diagnose_matrix=inspect_matrix)
    try:
        exec(compile(main.read_text(encoding='utf-8-sig'),str(main),'exec'),namespace)
    except MatrixBuilt:
        return
    raise ValueError('Native driver returned without a matrix')


if __name__ == '__main__':
    try:
        run()
    except BaseException:
        save(ROOT/'diagnostic_error.json',dict(utc=datetime.now(timezone.utc).isoformat(),error=traceback.format_exc()))
        raise

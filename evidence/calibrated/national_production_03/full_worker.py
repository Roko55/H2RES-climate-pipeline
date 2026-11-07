"""Full native rolling path, with atomic per-window exports and restart checkpoints."""

from pathlib import Path

import ast, csv, gzip, hashlib, inspect, importlib.util, json, os, pickle, sys, traceback

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent

if os.name != 'nt' or ROOT != Path('E:/Roko/remote_access/national_production_03'):
    raise ValueError("Use the new isolated Roko production project")

sys.path.insert(0, 'E:/Roko/remote_access/energy_rebuild_20261019/runtime_packages')

import numpy as np

import gurobipy as gp

MAPPING = ROOT / 'baseline/legacy_whole_path_03/mapping_20261030'

case = sys.argv[2]

job = sys.argv[3]

matched = int(sys.argv[4])

if matched or job != case or case not in ('average', 'solar_low', 'solar_high', 'wind_low', 'wind_high', 'temperature_cold', 'temperature_hot'):
    raise ValueError("Use one isolated stationary job per case")

assert case in ['average', 'solar_low', 'solar_high', 'wind_low', 'wind_high', 'temperature_cold', 'temperature_hot']

PROJECT = ROOT / 'projects' / case

JOB = ROOT / 'jobs' / job



YEARS = list(range(2020, 2051, 5))

OUT = JOB

year = 2020

source = ast.parse((ROOT / 'audited_export_helpers.py').read_text(encoding='utf8'))

defs = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in ('sha', 'clean', 'digest', 'save', 'DataOnly', '_control_after')]

assert len(defs) == 6

for node in defs:

    if isinstance(node, ast.FunctionDef) and node.name == '_control_after':

        for item in ast.walk(node):

            if isinstance(item, ast.Assign) and any((isinstance(t, ast.Name) and t.id == 'families' for t in item.targets)):

                item.value.elts += [ast.Constant(v) for v in ['heat_input', 'heat_sto_level', 'heat_pump_out', 'heat_pump_cap', 'heat_pump_sto_soc', 'cooling_pump_ind', 'cooling_pump_dh', 'ev_sto_in', 'ev_sto_out', 'ev_sto_soc', 'stat_sto_in', 'stat_sto_out', 'stat_sto_soc', 'storage_level', 'sto_out_flow', 'hydro_grid_pumping', 'H2tInd', 'ResToHeat', 'h2_sto_soc', 'h2_sto_out', 'extra_cap_inv']]

for _node in defs:

    if isinstance(_node, ast.FunctionDef) and _node.name == '_control_after':

        _node.body = [_part for _part in _node.body if not (isinstance(_part, ast.If) and ast.unparse(_part.test) == "case == 'avg'")]

module = ast.Module(body=defs, type_ignores=[])

ast.fix_missing_locations(module)

exec(compile(module, 'audited_export_helpers', 'exec'), globals())

from common import save as common_save, replace as safe_replace, OLD

from hooks import before as channel_before, prebuild as channel_prebuild, terms as channel_terms, ReferenceDone



def save(name, v):

    from common import clean as json_clean
    common_save(OUT / name, json_clean(v))

reference_mode = bool(matched)



def _channel_prebuild(ctx):

    global spatial_evidence
    require_stock_input(PROJECT,ctx['genco_dat'],base['production_inventory_sha256'])
    if spatial_evidence is None:
        from spatial_mapping_preflight import audit_resolved_spatial_paths
        from scripts import scenario_manager as manager
        spatial_evidence = audit_resolved_spatial_paths(
            manager, PROJECT, project_files, PROJECT/'spatial_sources.json', ctx['wind_resolution_mode'],
            inventory=production_supply, genco_path=ctx['genco_dat'])
    install_storage_retirement(ctx,storage_policy)
    prepare_hydrogen_scope(ctx,PROJECT,project_files)
    prepare_district_heat(ctx,district_policy)
    verify_heat_meter_input(ctx,PROJECT,project_files,heat_meter)
    verify_window_reference(ROOT,prepared['frozen_files'],ctx['years'])
    channel_prebuild(ctx, globals())
    ctx['_fixed_demand_originals'] = install_inputs(ctx,fixed_demand)
    prepare_hydro_window(ctx,hydro_resource)
    current = int(ctx['years'][0])
    i = int(ctx['i'])
    inherited = dataread(JOB/str(current)/f'inherited_{current}.pkl')['state']
    parent = snapshot if i == start else dataread(JOB/str(YEARS[i-1])/'after_window.pkl')
    if inherited != frozen_state(ctx) or parent is None or inherited != parent['state']:
        raise ValueError('Built planning window has the wrong inherited capital')
    common_save(JOB/str(current)/'spatial_input_audit.json', spatial_evidence)



def dataread(path):

    with path.open('rb') as f:

        return DataOnly(f).load()



def constant(v):

    if isinstance(v, (int, float, np.integer, np.floating)):

        return float(v)

    if hasattr(v, 'getValue') and v.size() == 0:

        return float(v.getValue())

    raise TypeError('Non-constant inherited state')



def frozen_state(ctx):

    return {k: {key: constant(v) for key, v in values.items()} for k, values in ctx.items() if isinstance(values, dict) and (k.endswith('_cumulative') or k.startswith('values_'))}

prepared = json.loads((ROOT / 'preparation.json').read_text())

fence = prepared['execution_fence']

from multisector_planning_integration import (
    audit_stationary_plan, load_approved_base, load_supply_manifest, require_demand_evidence, require_stock_input,
    verify_window_reference,
)
from audit_production_families import audit_built_supply
from verify_planning_capital import branch_snapshot, resume_accepted_windows, audit_capital_handoff
from multisector_fixed_demand import (
    load_schedules, apply_heat_meter_split, verify_heat_meter_input, install_inputs,
    verify_and_fix_built_demand, audit_solved_demand,
)
from multisector_stationary_storage import (
    load_storage_policy, install_storage_retirement, install_storage_limits, audit_storage_solution,
)
from multisector_hydro_integration import (
    load_hydro_inputs, prepare_hydro_window, install_and_audit_hydro, audit_hydro_solution,
)
from multisector_hydrogen_scope import prepare_hydrogen_scope, audit_built_hydrogen_scope, audit_solved_hydrogen_scope
from multisector_power_policy import audit_native_power_policy, audit_solved_power_policy, load_gas_stock
from multisector_dispatchable_renewables import (
    load_dispatchable_renewables, audit_and_limit_dispatchable_renewables, audit_solved_dispatchable_renewables,
)
from multisector_district_heat import (
    load_district_heat_policy, prepare_district_heat, install_district_heat_rows, install_district_fuel,
    audit_district_heat_solution,
)
from multisector_exchange_integration import (
    load_country_exchange_inputs, install_and_audit_country_exchange, audit_solved_exchange,
)

audit_stationary_plan(ROOT,prepared)
base_digest = prepared['historical_base_sha256']
base = load_approved_base(ROOT,base_digest)
require_demand_evidence(ROOT,prepared,case)
production_supply = load_supply_manifest(PROJECT,base['production_supply_inventory_sha256'])
project_prefix = f'projects/{case}/'
project_files = {key[len(project_prefix):]:value for key,value in prepared['frozen_files'].items()
                 if key.startswith(project_prefix)}
country_hourly, country_price = load_country_exchange_inputs(PROJECT,project_files)
fixed_demand = load_schedules(PROJECT,project_files,case)
fixed_demand, heat_meter = apply_heat_meter_split(PROJECT,project_files,fixed_demand)
storage_policy = load_storage_policy(PROJECT,project_files)
hydro_resource, hydro_policy = load_hydro_inputs(PROJECT,project_files)
district_policy = load_district_heat_policy(PROJECT,project_files)
gas_stock = load_gas_stock(PROJECT,project_files)
dispatchable_renewables = load_dispatchable_renewables(PROJECT,project_files)
spatial_evidence = None
JOB.mkdir(parents=True,exist_ok=True)
start, snapshot = resume_accepted_windows(JOB,case,fence,base_digest,dataread)
if start == len(YEARS):
    save('completed.json', {'status':'completed_checked','case':case,'job':job,
         'years':YEARS[1:],'fence':fence,'historical_base_sha256':base_digest,'independent_path':True})
    raise SystemExit(0)

os.environ['FULL_START_INDEX'] = str(start)

os.environ['FULL_MATCHED_YEAR'] = str(matched)

settings = json.loads((MAPPING / 'projects/repaired_avg/review_command.json').read_text())['settings']

for key in list(os.environ):

    if key.startswith(('H2RES_', 'LEGACY_')):

        os.environ.pop(key)

settings.update({'LEGACY_AUDIT_DIR': str(JOB), 'H2RES_CLIMATE_SCENARIO': 'avg_yield__normal' if case == 'average' else case + '__channel', 'H2RES_RUN_LABEL': job, 'H2RES_PROFILE_OVERRIDES': '', 'H2RES_SOLVER_SUMMARY_DIR': str(JOB), 'H2RES_CAPACITY_EXPORT_DIR': str(JOB)})

os.environ.update(settings)



def _full_restore(ctx):
    global snapshot
    if snapshot is None:
        snapshot = branch_snapshot(ctx,base)

    assert ctx['i'] == start

    assert list(ctx['hor_yrs']) == snapshot['hor_yrs']

    actual = {k for k, v in ctx.items() if isinstance(v, dict) and (k.endswith('_cumulative') or k.startswith('values_'))}

    assert actual == set(snapshot['state'])

    for name, value in snapshot['state'].items():

        ctx[name] = dict(value)

    assert digest(frozen_state(ctx)) == digest(snapshot['state'])



def _control_before(model, ctx):

    supply_audit = audit_built_supply(ctx,model,production_supply)
    exchange_audit = install_and_audit_country_exchange(ctx,country_hourly,country_price)
    demand_audit = verify_and_fix_built_demand(ctx,fixed_demand)
    storage_audit = install_storage_limits(ctx,storage_policy)
    hydro_audit = install_and_audit_hydro(ctx,hydro_policy)
    hydrogen_audit = audit_built_hydrogen_scope(ctx)
    district_audit = install_district_heat_rows(ctx,district_policy)
    district_audit['fuel'] = install_district_fuel(ctx,district_policy)
    policy_audit = audit_native_power_policy(ctx,gas_stock)
    bio_audit = audit_and_limit_dispatchable_renewables(ctx,dispatchable_renewables)
    channel_before(model, ctx, globals())
    save('production_supply_audit.json', supply_audit)
    save('country_exchange_build_audit.json', exchange_audit)
    save('fixed_demand_build_audit.json', demand_audit)
    save('stationary_storage_build_audit.json', storage_audit)
    save('hydro_build_audit.json', hydro_audit)
    save('hydrogen_build_audit.json', hydrogen_audit)
    save('district_heat_build_audit.json', district_audit)
    save('power_policy_build_audit.json', policy_audit)
    save('dispatchable_renewables_build_audit.json', bio_audit)



def _full_checkpoint(ctx):

    assert year == int(ctx['years'][0])

    model = ctx['ucp']

    variables = model.getVars()

    coeffs = model.getAttr('Obj', variables)

    values = model.getAttr('X', variables)

    ledger = {str(int(y)): 0.0 for y in ctx['years']}

    unassigned = 0.0

    for variable, coefficient, value in zip(variables, coeffs, values):

        if coefficient == 0:

            continue

        token = variable.VarName.rsplit(',', 1)[-1].rsplit('[', 1)[-1].rstrip(']')

        if token in ledger:

            ledger[token] += coefficient * value

        else:

            unassigned += coefficient * value

    constant_term = model.getObjective().getConstant()

    gap = sum(ledger.values()) + unassigned + constant_term - model.ObjVal

    assert abs(gap) < 0.01, 'Annual objective attribution does not reconcile'

    save('annual_objective_ledger.json', {'values_by_anchor': ledger, 'constant': constant_term, 'unassigned': unassigned, 'objective': model.ObjVal, 'gap': gap, 'NPV': {str(y): float(ctx['NPV'][y]) for y in ctx['years']}, 'meaning': 'Original objective contributions by anchor, including one-off investment; not annualized system cost or five-year integral'})

    with gzip.open(OUT / 'keyed_constraints.csv.gz', 'wt', encoding='utf8', newline='') as f:

        writer = csv.writer(f)

        writer.writerow(['family', 'key_json', 'constraint', 'sense', 'rhs', 'slack', 'dual'])

        for name, dic in list(ctx.items()):

            if not isinstance(dic, dict) or not dic:

                continue

            if not isinstance(next(iter(dic.values())), gp.Constr):

                continue

            for key, constraint in dic.items():

                if isinstance(constraint, gp.Constr):

                    writer.writerow([name, json.dumps(key, default=lambda x: x.item()), constraint.ConstrName, constraint.Sense, constraint.RHS, constraint.Slack, constraint.Pi])

    selected = [r for r in ctx['objective_breakdown_results'] if int(r['year']) == year]

    assert selected

    ctx['_write_objective_breakdown_outputs'](str(OUT / 'objective_breakdown_outputs.csv'), selected)

    result = json.loads((OUT / 'solver_result.json').read_text())

    active = sum((float(r['value']) for r in selected if int(r['active_in_objective']) == 1))

    assert abs(active - result['objective']) < 0.01, 'Objective components do not reconcile'

    after = {'state': frozen_state(ctx), 'hor_yrs': [int(y) for y in ctx['hor_yrs']], 'years': [int(y) for y in ctx['years']], 'next_index': int(ctx['i']) + 1, 'case': case, 'job': job, 'fence': fence}

    if (result['status'] != 2 or result['window_years'] != list(ctx['years'])
            or not all(0 <= result[k] <= 1e-7 for k in ('constraint_violation','bound_violation'))):
        raise ValueError('Window lacks a strict optimal result')
    inherited = dataread(OUT / f'inherited_{year}.pkl')['state']
    after['historical_base_sha256'] = base_digest
    save('capital_handoff_audit.json', audit_capital_handoff(ctx,inherited,after['state']))
    save('country_exchange_solution_audit.json', audit_solved_exchange(ctx))
    save('fixed_demand_solution_audit.json', audit_solved_demand(ctx,fixed_demand))
    save('stationary_storage_solution_audit.json', audit_storage_solution(ctx))
    save('hydro_solution_audit.json', audit_hydro_solution(ctx))
    save('hydrogen_solution_audit.json', audit_solved_hydrogen_scope(ctx))
    save('district_heat_solution_audit.json', audit_district_heat_solution(ctx,district_policy))
    save('power_policy_solution_audit.json', audit_solved_power_policy(ctx))
    save('dispatchable_renewables_solution_audit.json', audit_solved_dispatchable_renewables(ctx,dispatchable_renewables))
    temp = OUT / 'after_window.pkl.pending' 

    with temp.open('wb') as f:

        pickle.dump(after, f, protocol=4)

    safe_replace(temp, OUT / 'after_window.pkl')

    save('solver_summary.json', [r for r in ctx['solver_subproblem_summary'] if int(r['planning_year']) == year])

    channel_terms(ctx, model, OUT)

    expected = ['solver_result.json', 'input_audit.json', 'objective_breakdown_outputs.csv', 'annual_objective_ledger.json', 'after_window.pkl', 'runtime_accounting_inputs.csv.gz', 'all_variables.csv.gz', 'all_constraints.csv.gz', 'keyed_constraints.csv.gz', 'keyed_outputs.csv.gz', f'inherited_{year}.pkl', 'prebuild_validation.json', 'investment_terms.csv', 'capital_handoff_audit.json', 'spatial_input_audit.json', 'production_supply_audit.json', 'country_exchange_build_audit.json', 'country_exchange_solution_audit.json', 'fixed_demand_build_audit.json', 'fixed_demand_solution_audit.json', 'stationary_storage_build_audit.json', 'stationary_storage_solution_audit.json', 'hydro_build_audit.json', 'hydro_solution_audit.json', 'hydrogen_build_audit.json', 'hydrogen_solution_audit.json', 'district_heat_build_audit.json', 'district_heat_solution_audit.json', 'power_policy_build_audit.json', 'power_policy_solution_audit.json', 'dispatchable_renewables_build_audit.json', 'dispatchable_renewables_solution_audit.json']

    save('accepted.json', {'case': case, 'job': job, 'year': year, 'fence': fence, 'objective_gap': active - result['objective'], 'historical_base_sha256': base_digest, 'files': {n: sha(OUT / n) for n in expected}})

    save('progress.json', {'status': 'accepted', 'year': year, 'next_index': after['next_index']})
    ctx.update(ctx.pop('_fixed_demand_originals'))
    ctx.update(ctx.pop('_hydro_originals'))

try:

    pp = MAPPING.parents[1] / 'helpers/precision_policy.py'

    tree = ast.parse(pp.read_text())

    count = 0

    for node in ast.walk(tree):

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and (node.value.func.attr == 'relative_to') and isinstance(node.value.func.value, ast.Name) and (node.value.func.value.id == 'record_path'):

            node.value.args = [ast.parse("Path('E:/Roko/remote_access/national_production_03').resolve()", mode='eval').body]

            count += 1

    assert count == 1

    ast.fix_missing_locations(tree)

    policy_file = ROOT / ('precision_policy_owned_' + case + '.py')

    policy_file.write_text(ast.unparse(tree) + '\n')

    spec = importlib.util.spec_from_file_location('precision_policy', policy_file)

    m = importlib.util.module_from_spec(spec)

    sys.modules['precision_policy'] = m

    spec.loader.exec_module(m)

    legacy = ast.parse((MAPPING / 'results/repaired_avg/legacy_runner.py').read_text())

    new = []

    for node in legacy.body:

        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) and (node.target.id == 'target'):

            new += ast.parse('assert PROJECT.is_relative_to(ROOT) and OUT.is_relative_to(ROOT)').body

            continue

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and (node.value.func.attr == 'run_path'):

            continue

        if isinstance(node, ast.ClassDef) and node.name == 'AuditedModel':

            method = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'optimize'))

            method.body.insert(1, ast.parse('_control_before(self,inspect.currentframe().f_back.f_globals)').body[0])

            method.body += ast.parse('_control_after(self,inspect.currentframe().f_back.f_globals)').body

        new.append(node)

    legacy.body = new

    ast.fix_missing_locations(legacy)

    sys.argv[1] = str(PROJECT)

    exec(compile(legacy, 'audited_quality_wrapper', 'exec'), globals())

    OUT = JOB

    os.chdir(PROJECT)

    sys.path.insert(0, str(PROJECT))

    namespace = {'__name__': '__main__', '__file__': str(PROJECT / 'main_rolling_horizon_v3.py'), '_full_restore': _full_restore, '_full_checkpoint': _full_checkpoint, '_channel_prebuild': _channel_prebuild}

    exec(compile((PROJECT / 'main_rolling_horizon_v3.py').read_text(encoding='utf8'), str(PROJECT / 'main_rolling_horizon_v3.py'), 'exec'), namespace)

    targets = YEARS[1:]

    assert all(((JOB / str(y) / 'accepted.json').exists() for y in targets))

    OUT = JOB

    save('completed.json', {'status': 'completed_checked', 'case': case, 'job': job, 'years': targets, 'fence': fence, 'historical_base_sha256': base_digest, 'independent_path': True})

except ReferenceDone:

    OUT = JOB

    save('reference_completed.json', {'status': 'reference_checked', 'year': matched, 'fence': fence})

except BaseException:

    OUT = JOB

    save('failure.json', {'traceback': traceback.format_exc(), 'case': case, 'job': job, 'year': year, 'fence': fence})

    raise


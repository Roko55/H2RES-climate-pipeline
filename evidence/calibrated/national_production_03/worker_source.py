"""Full native rolling path, with atomic per-window exports and restart checkpoints."""
from pathlib import Path
import ast, csv, gzip, hashlib, inspect, importlib.util, json, os, pickle, sys, traceback
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
assert ROOT == Path('E:/Roko/remote_access/channel_investments_20261101')
sys.path.insert(0, 'E:/Roko/remote_access/energy_rebuild_20261019/runtime_packages')
import numpy as np
import gurobipy as gp
MAPPING = ROOT / 'baseline/legacy_whole_path_03/mapping_20261030'
case = sys.argv[2]
job = sys.argv[3]
matched = int(sys.argv[4])
assert case in ['average', 'solar_low', 'solar_high', 'wind_low', 'wind_high', 'temperature_cold', 'temperature_hot']
PROJECT = ROOT / 'projects' / case
JOB = ROOT / 'jobs' / job
JOB.mkdir(parents=True, exist_ok=True)
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
                item.value.elts += [ast.Constant(v) for v in ['heat_input', 'heat_sto_level', 'heat_pump_out', 'heat_pump_cap', 'heat_pump_sto_soc', 'cooling_pump_ind', 'cooling_pump_dh', 'ev_sto_in', 'ev_sto_out', 'ev_sto_soc', 'stat_sto_in', 'stat_sto_out', 'stat_sto_soc', 'storage_level', 'sto_out_flow', 'H2tInd', 'ResToHeat', 'h2_sto_soc', 'h2_sto_out', 'extra_cap_inv']]
for _node in defs:
    if isinstance(_node, ast.FunctionDef) and _node.name == '_control_after':
        _node.body = [_part for _part in _node.body if not (isinstance(_part, ast.If) and ast.unparse(_part.test) == "case == 'avg'")]
module = ast.Module(body=defs, type_ignores=[])
ast.fix_missing_locations(module)
exec(compile(module, 'audited_export_helpers', 'exec'), globals())
from common import save as common_save, replace as safe_replace, OLD
from hooks import before as channel_before, prebuild as channel_prebuild, terms as channel_terms, ReferenceDone

def save(name, v):
    common_save(OUT / name, v)
reference_mode = bool(matched)

def _channel_prebuild(ctx):
    channel_prebuild(ctx, globals())

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
accepted = []
for y in YEARS:
    marker = JOB / str(y) / 'accepted.json'
    if not marker.exists():
        break
    rec = json.loads(marker.read_text())
    assert rec['fence'] == fence and rec['case'] == case and (rec['year'] == y)
    for file, digest_ in rec['files'].items():
        assert sha(JOB / str(y) / file) == digest_, file
    accepted.append(y)
if matched:
    assert reference_mode and case == 'average' and (matched in YEARS)
    accepted = []
    start = YEARS.index(matched)
    snapshot = dataread(OLD / 'multisector/jobs/avg_path' / str(matched) / f'inherited_{matched}.pkl')
elif accepted:
    start = len(accepted)
    snapshot = dataread(JOB / str(accepted[-1]) / 'after_window.pkl')
else:
    start = 0
    snapshot = dataread(MAPPING / 'results/repaired_avg/quality/inherited_2020.pkl')
assert start <= 7
os.environ['FULL_START_INDEX'] = str(start)
os.environ['FULL_MATCHED_YEAR'] = str(matched)
settings = json.loads((MAPPING / 'projects/repaired_avg/review_command.json').read_text())['settings']
for key in list(os.environ):
    if key.startswith(('H2RES_', 'LEGACY_')):
        os.environ.pop(key)
settings.update({'LEGACY_AUDIT_DIR': str(JOB), 'H2RES_CLIMATE_SCENARIO': 'avg_yield__normal' if case == 'average' else case + '__channel', 'H2RES_RUN_LABEL': job, 'H2RES_PROFILE_OVERRIDES': '', 'H2RES_SOLVER_SUMMARY_DIR': str(JOB), 'H2RES_CAPACITY_EXPORT_DIR': str(JOB)})
os.environ.update(settings)

def _full_restore(ctx):
    assert ctx['i'] == start
    assert list(ctx['hor_yrs']) == snapshot['hor_yrs']
    actual = {k for k, v in ctx.items() if isinstance(v, dict) and (k.endswith('_cumulative') or k.startswith('values_'))}
    assert actual == set(snapshot['state'])
    for name, value in snapshot['state'].items():
        ctx[name] = dict(value)
    assert digest(frozen_state(ctx)) == digest(snapshot['state'])

def _control_before(model, ctx):
    channel_before(model, ctx, globals())

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
    temp = OUT / 'after_window.pkl.pending'
    with temp.open('wb') as f:
        pickle.dump(after, f, protocol=4)
    safe_replace(temp, OUT / 'after_window.pkl')
    save('solver_summary.json', [r for r in ctx['solver_subproblem_summary'] if int(r['planning_year']) == year])
    channel_terms(ctx, model, OUT)
    expected = ['solver_result.json', 'input_audit.json', 'objective_breakdown_outputs.csv', 'annual_objective_ledger.json', 'after_window.pkl', 'runtime_accounting_inputs.csv.gz', 'all_variables.csv.gz', 'all_constraints.csv.gz', 'keyed_constraints.csv.gz', 'keyed_outputs.csv.gz', f'inherited_{year}.pkl', 'prebuild_validation.json', 'investment_terms.csv']
    save('accepted.json', {'case': case, 'job': job, 'year': year, 'fence': fence, 'objective_gap': active - result['objective'], 'files': {n: sha(OUT / n) for n in expected}})
    save('progress.json', {'status': 'accepted', 'year': year, 'next_index': after['next_index']})
try:
    pp = MAPPING.parents[1] / 'helpers/precision_policy.py'
    tree = ast.parse(pp.read_text())
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and (node.value.func.attr == 'relative_to') and isinstance(node.value.func.value, ast.Name) and (node.value.func.value.id == 'record_path'):
            node.value.args = [ast.parse("Path('E:/Roko/remote_access/channel_investments_20261101').resolve()", mode='eval').body]
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
    targets = [matched] if matched else YEARS
    assert all(((JOB / str(y) / 'accepted.json').exists() for y in targets))
    OUT = JOB
    save('completed.json', {'status': 'completed_checked', 'case': case, 'job': job, 'years': targets, 'fence': fence, 'independent_path': not bool(matched)})
except ReferenceDone:
    OUT = JOB
    save('reference_completed.json', {'status': 'reference_checked', 'year': matched, 'fence': fence})
except BaseException:
    OUT = JOB
    save('failure.json', {'traceback': traceback.format_exc(), 'case': case, 'job': job, 'year': year, 'fence': fence})
    raise

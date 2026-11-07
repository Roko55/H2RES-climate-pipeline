"""Render the isolated multisector worker with a qualified 2020 capital seed."""
import ast
import json
from math import isfinite
from pathlib import Path

from electricity_project_integration import digest_file
from verify_planning_capital import HORIZON, TOL


ROKO = Path('E:/Roko/remote_access')
OLD_ROOT = 'E:/Roko/remote_access/channel_investments_20261101'
CASES = ('average','solar_low','solar_high','wind_low','wind_high','temperature_cold','temperature_hot')


def load_approved_base(root, expected_sha):
    path = root/'historical_base.json'
    if not path.is_file() or digest_file(path) != expected_sha:
        raise ValueError('Missing or changed historical production base')
    base = json.loads(path.read_bytes())
    if (base.get('year') != 2020 or base.get('scope') != 'multisector'
            or base.get('planning_approved') is not True or base.get('status') != 'accepted'
            or base.get('solver_status') != 2 or not 0 <= base['maximum_new_capacity'] <= TOL):
        raise ValueError('The 2020 production base is not qualified for planning')
    required = {'historical_terminal','independent_physical_audit','production_stock_reconciliation'}
    evidence = base.get('evidence_sha256',{})
    if evidence.keys() != required:
        raise ValueError('Historical production base lacks independent stock and physical evidence')
    seen = set()
    reports = {}
    for name,item in evidence.items():
        rel = Path(item['path'])
        file = (root/rel).resolve()
        if rel.is_absolute() or not file.is_relative_to(root) or file in seen or file == path:
            raise ValueError('Invalid historical evidence path')
        if digest_file(file) != item['sha256']:
            raise ValueError('Historical production evidence changed: '+str(rel))
        seen.add(file)
        reports[name] = json.loads(file.read_bytes())
    terminal = reports['historical_terminal']
    physical = reports['independent_physical_audit']
    stock = reports['production_stock_reconciliation']
    inventory_sha = stock.get('production_inventory_sha256','')
    supply_sha = stock.get('production_supply_inventory_sha256','')
    gates = ('power_balance','heat_balance','storage_balance','exchange','power_co2','heat_co2')
    if (terminal.get('status') != 'solved' or terminal.get('solver_status') != 2
            or terminal.get('maximum_new_capacity') != base['maximum_new_capacity']
            or not all(0 <= terminal[k] <= TOL for k in ('constraint_violation','bound_violation'))
            or physical.get('status') != 'passed' or physical.get('hourly_count') != 8760
            or not all(isfinite(physical['max_residual'][key]) and 0 <= physical['max_residual'][key] <= TOL
                       for key in gates)
            or stock.get('status') != 'passed' or stock.get('scope') != 'multisector'
            or stock.get('all_native_families_reconciled') is not True
            or inventory_sha != base.get('production_inventory_sha256')
            or supply_sha != base.get('production_supply_inventory_sha256')
            or any(len(d) != 64 or any(c not in '0123456789abcdef' for c in d) for d in (inventory_sha,supply_sha))):
        raise ValueError('Historical terminal, physical or production stock evidence did not qualify')
    return base


def audit_stationary_plan(root, preparation):
    path = root/'plan.json'
    if (preparation.get('stationary_channel_design') is not True
            or digest_file(path) != preparation['frozen_files']['plan.json']):
        raise ValueError('Missing frozen stationary production design')
    plan = json.loads(path.read_bytes())
    if ({item['id'] for item in plan['cases']} != set(CASES) or len(plan['cases']) != len(CASES)
            or plan['climate_adjustments'] != dict(solar_factor_2050=1.,wind_factor_2050=1.,temp_offset_2050=0.)
            or plan['required_new_planning_windows'] != len(CASES)*(len(HORIZON)-1)):
        raise ValueError('Expected seven stationary paths from 2025 to 2050')


def require_demand_evidence(root, preparation, case):
    relative = f'projects/{case}/future_demand_physical_audit.json'
    path = root/relative
    frozen = preparation['frozen_files']
    if not path.is_file() or digest_file(path) != frozen.get(relative):
        raise ValueError('Future EV and cooling inputs have no frozen physical acceptance')
    report = json.loads(path.read_bytes())
    if (report.get('status') != 'approved_for_planning' or report.get('case') != case
            or report.get('exogenous_power_excludes_native_hydro_pumping') is not True
            or [x['year'] for x in report['windows']] != list(HORIZON[1:])
            or any(x['status'] != 'input_physical_checks_passed' or x['hours'] != 8760 for x in report['windows'])):
        raise ValueError('One or more future EV and cooling windows did not qualify')
    for section in ('independent_limits','runtime_input_manifest'):
        item = report[section]
        relative = Path(item['path'])
        asset = (path.parent/relative).resolve()
        if (relative.is_absolute() or not asset.is_relative_to(path.parent.resolve())
                or item['sha256'] != frozen.get(asset.relative_to(root.resolve()).as_posix())
                or digest_file(asset) != item['sha256']):
            raise ValueError('Future demand source or physical limits changed: '+section)


def require_stock_input(project, source, expected_sha):
    asset = (project/source).resolve()
    if (not asset.is_relative_to(project.resolve()) or not asset.is_file()
            or digest_file(asset) != expected_sha):
        raise ValueError('Actual model inventory differs from the qualified physical stock')


def load_supply_manifest(project, expected_sha):
    path = (project/'production_supply_inventory.json').resolve()
    if path.parent != project.resolve() or not path.is_file() or digest_file(path) != expected_sha:
        raise ValueError('Approved production supply inventory is missing or changed')
    return json.loads(path.read_bytes())


def verify_window_reference(root, frozen, years):
    relative = f'reference/{years[0]}/prebuild_validation.json'
    path = (root/relative).resolve()
    if (not path.is_relative_to(root.resolve()) or not path.is_file()
            or digest_file(path) != frozen.get(relative)):
        raise ValueError(f'Missing or changed common-input reference for {years[0]}')
    report = json.loads(path.read_bytes())
    if (report.get('case') != 'average' or report.get('window_years') != list(years)
            or not report.get('common_input_hashes') or not report.get('thermal_hashes')):
        raise ValueError(f'Invalid common-input reference for {years[0]}')


def render_worker(source, new_root):
    """Use only in an isolated copy of the stationary source, never in archives."""
    root = str(new_root).replace('\\','/')
    if (not root.startswith(str(ROKO).replace('\\','/')+'/') or root == OLD_ROOT
            or '..' in root.split('/') or root.rstrip('/') != root):
        raise ValueError('Use a new isolated Roko project root')
    if source.count(OLD_ROOT) != 2:
        raise ValueError('Unexpected archived worker root declarations')
    source = source.replace(OLD_ROOT,root)

    def replace(old,new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError('Unexpected multisector worker anchor: '+old)
        source = source.replace(old,new)

    replace("'sto_out_flow', 'H2tInd'", "'sto_out_flow', 'hydro_grid_pumping', 'H2tInd'")
    replace('    common_save(OUT / name, v)', '    from common import clean as json_clean\n'
            '    common_save(OUT / name, json_clean(v))')

    replace('matched = int(sys.argv[4])', 'matched = int(sys.argv[4])\n\n'
            f'if matched or job != case or case not in {CASES!r}:\n'
            '    raise ValueError("Use one isolated stationary job per case")')
    replace(f"assert ROOT == Path('{root}')", f"if os.name != 'nt' or ROOT != Path({root!r}):\n"
            '    raise ValueError("Use the new isolated Roko production project")')
    replace('JOB.mkdir(parents=True, exist_ok=True)', '')
    start = source.index('accepted = []')
    end = source.index("os.environ['FULL_START_INDEX']",start)
    if source[start:end].count('inherited_2020.pkl') != 1:
        raise ValueError('Cannot identify the old resume and archived 2020 fallback block')
    source = source[:start]+'''from multisector_planning_integration import (
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

'''+source[end:]
    replace('def _full_restore(ctx):', 'def _full_restore(ctx):\n'
            '    global snapshot\n'
            '    if snapshot is None:\n        snapshot = branch_snapshot(ctx,base)')
    replace('    channel_before(model, ctx, globals())', '''    supply_audit = audit_built_supply(ctx,model,production_supply)
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
    save('dispatchable_renewables_build_audit.json', bio_audit)''')
    replace('    channel_prebuild(ctx, globals())', '''    global spatial_evidence
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
    common_save(JOB/str(current)/'spatial_input_audit.json', spatial_evidence)''')
    replace("    temp = OUT / 'after_window.pkl.pending'", '''    if (result['status'] != 2 or result['window_years'] != list(ctx['years'])
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
    temp = OUT / 'after_window.pkl.pending' ''')
    replace("'prebuild_validation.json', 'investment_terms.csv']", "'prebuild_validation.json', 'investment_terms.csv', 'capital_handoff_audit.json', 'spatial_input_audit.json', 'production_supply_audit.json', 'country_exchange_build_audit.json', 'country_exchange_solution_audit.json', 'fixed_demand_build_audit.json', 'fixed_demand_solution_audit.json', 'stationary_storage_build_audit.json', 'stationary_storage_solution_audit.json', 'hydro_build_audit.json', 'hydro_solution_audit.json', 'hydrogen_build_audit.json', 'hydrogen_solution_audit.json', 'district_heat_build_audit.json', 'district_heat_solution_audit.json', 'power_policy_build_audit.json', 'power_policy_solution_audit.json', 'dispatchable_renewables_build_audit.json', 'dispatchable_renewables_solution_audit.json']")
    replace("'objective_gap': active - result['objective'], 'files':", "'objective_gap': active - result['objective'], 'historical_base_sha256': base_digest, 'files':")
    progress = "    save('progress.json', {'status': 'accepted', 'year': year, 'next_index': after['next_index']})"
    replace(progress, progress+"\n    ctx.update(ctx.pop('_fixed_demand_originals'))\n    ctx.update(ctx.pop('_hydro_originals'))")
    replace('targets = [matched] if matched else YEARS', 'targets = YEARS[1:]')
    replace("'fence': fence, 'independent_path': not bool(matched)", "'fence': fence, 'historical_base_sha256': base_digest, 'independent_path': True")
    ast.parse(source)
    return source


def render_reference_worker(source, new_root):
    """Capture a common-input receipt from the average path, before model construction."""
    code = render_worker(source, new_root)

    def replace(old, new):
        nonlocal code
        if code.count(old) != 1:
            raise ValueError('Unexpected input-reference anchor: '+old)
        code = code.replace(old, new)

    replace(f'if matched or job != case or case not in {CASES!r}:\n'
            '    raise ValueError("Use one isolated stationary job per case")',
            "if case != 'average' or job != case or matched not in range(2025,2051,5):\n"
            "    raise ValueError('Use the average path and one future reference anchor')")
    replace("JOB = ROOT / 'jobs' / job", "JOB = ROOT / 'input_reference_capture' / str(matched)")
    replace('JOB.mkdir(parents=True,exist_ok=True)', 'JOB.mkdir(parents=True,exist_ok=False)')
    start = code.index('start, snapshot = resume_accepted_windows')
    end = code.index("os.environ['FULL_START_INDEX']", start)
    code = code[:start]+'start, snapshot = YEARS.index(matched), None\n\n'+code[end:]
    start = code.index('def _full_restore(ctx):')
    end = code.index('def _control_before(',start)
    code = code[:start]+'''def _full_restore(ctx):
    if ctx['i'] != start or list(ctx['hor_yrs']) != YEARS or any(
            v != 0. for values in frozen_state(ctx).values() for v in values.values()):
        raise ValueError('Input-reference capture requires a fresh unused native context')


'''+code[end:]
    replace('def _channel_prebuild(ctx):', 'class InputReferenceDone(BaseException):\n    pass\n\n\ndef _channel_prebuild(ctx):')
    replace("    verify_window_reference(ROOT,prepared['frozen_files'],ctx['years'])\n"
            '    channel_prebuild(ctx, globals())',
            '    channel_prebuild(ctx, globals())\n    raise InputReferenceDone()')
    replace("            method.body.insert(1, ast.parse('_control_before(self,inspect.currentframe().f_back.f_globals)').body[0])\n\n"
            "            method.body += ast.parse('_control_after(self,inspect.currentframe().f_back.f_globals)').body",
            "            method.body = ast.parse(\"raise ValueError('Input-reference capture cannot optimize')\").body")
    start = code.index('    targets = YEARS[1:]')
    end = code.index('except BaseException:', start)
    code = code[:start]+'''    raise ValueError('Native input-reference hook did not run')

except InputReferenceDone:
    receipt = JOB / str(matched) / 'prebuild_validation.json'
    if not receipt.is_file() or any(JOB.glob('*/accepted.json')):
        raise ValueError('Input-reference capture left no reference or an unexpected checkpoint')
    common_save(JOB/'capture.json', dict(status='common_input_reference_captured_no_build',
                year=matched, solver_invocations=0, planning_approved=False,
                execution_fence=fence, historical_base_sha256=base_digest, receipt_sha256=sha(receipt)))

'''+code[end:]
    ast.parse(code)
    return code


def render_dry_build_worker(source, new_root):
    """Render a one-window matrix build that cannot call the audited optimizer."""
    code = render_worker(source,new_root)

    def replace(old,new):
        nonlocal code
        if code.count(old) != 1:
            raise ValueError('Unexpected dry-build anchor: '+old)
        code = code.replace(old,new)

    replace("JOB = ROOT / 'jobs' / job", "JOB = ROOT / 'dry_build_jobs' / job")
    replace('JOB.mkdir(parents=True,exist_ok=True)', 'JOB.mkdir(parents=True,exist_ok=False)')
    replace('start, snapshot = resume_accepted_windows(JOB,case,fence,base_digest,dataread)',
            'start, snapshot = 1, None')
    start = code.index('if start == len(YEARS):')
    end = code.index("os.environ['FULL_START_INDEX']",start)
    code = code[:start]+code[end:]
    replace('def _control_before(model, ctx):', '''class DryBuildDone(BaseException):
    pass


def dry_build_complete(model, ctx):
    model.update()
    if tuple(ctx['years']) != (2025,2030) or int(ctx['i']) != 1 or model.SolCount != 0:
        raise ValueError('Unexpected dry-build year or previously solved model')
    common_save(JOB/'dry_build.json', dict(status='matrix_built_without_optimization',
                solver_invocations=0,planning_approved=False,case=case,fence=fence,
                historical_base_sha256=base_digest,years=list(ctx['years']),
                rows=model.NumConstrs,columns=model.NumVars,fingerprint=model.Fingerprint))
    raise DryBuildDone()


def _control_before(model, ctx):''')
    replace("            method.body.insert(1, ast.parse('_control_before(self,inspect.currentframe().f_back.f_globals)').body[0])\n\n"
            "            method.body += ast.parse('_control_after(self,inspect.currentframe().f_back.f_globals)').body",
            "            method.body = ast.parse('ctx = inspect.currentframe().f_back.f_globals\\n"
            "_control_before(self,ctx)\\ndry_build_complete(self,ctx)').body")
    replace("    exec(compile(legacy, 'audited_quality_wrapper', 'exec'), globals())",
            "    exec(compile(legacy, 'audited_quality_wrapper', 'exec'), globals())\n"
            "    if gp.Model is not AuditedModel:\n        raise ValueError('Dry-build optimizer guard is not installed')")
    replace('except ReferenceDone:', '''except DryBuildDone:
    if not (JOB/'dry_build.json').is_file() or any(JOB.glob('*/accepted.json')):
        raise ValueError('Dry build left an unexpected checkpoint or no matrix report')

except ReferenceDone:''')
    start = code.index('    targets = YEARS[1:]')
    end = code.index('except DryBuildDone:',start)
    code = code[:start]+"    raise ValueError('Native build returned without hitting the dry-build guard')\n\n"+code[end:]
    start = code.index('except ReferenceDone:')
    end = code.index('except BaseException:',start)
    code = code[:start]+code[end:]
    ast.parse(code)
    return code

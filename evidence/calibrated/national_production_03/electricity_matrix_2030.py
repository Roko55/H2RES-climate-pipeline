"""Build and audit one isolated native 2030 matrix, without optimizing."""
import ast
import csv
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from itertools import product
from math import isfinite

from electricity_krsko_preflight import audit_built_limits, electricity_candidate
from electricity_project_integration import NATIVE_INPUTS, SET_OBJECTIVE, digest_file
from electricity_window_preflight import render_signed_outside_prices
from eraa_2030_built_exchange import HOURS, apply_fixed_native_transfer_inputs, load_envelope
from eraa_electricity_integration import finish_eraa_2030


ROOT = Path(__file__).resolve().parent
REMOTE = Path('E:/Roko/remote_access')
ARCHIVE = REMOTE / 'final_frozen_profiles_20261031'
PROJECT = ROOT / 'project'
STATUS = ROOT / 'status.json'
EXPECTED_HOST = 'pcsp-mxl0493vpr'
CAPACITY_ROW_LABELS = {'max_inv_unit': 'diagnostic_new_cap',
                       'max_generator_capacity': 'diagnostic_total_cap'}


def save(**details):
    path = STATUS.with_suffix('.tmp')
    path.write_text(json.dumps(dict(utc=datetime.now(timezone.utc).isoformat(),
                                    pid=os.getpid(), planning_approved=False,
                                    optimize_called=False, **details), indent=2))
    path.replace(STATUS)


def resource_check():
    import psutil
    loaded = []
    inaccessible = 0
    for proc in psutil.process_iter(['name']):
        name = (proc.info['name'] or '').lower()
        if not name.startswith(('python', 'gurobi', 'grb')):
            continue
        if proc.pid == os.getpid():
            continue
        try:
            if name.startswith(('gurobi', 'grb')) or any(
                'gurobi' in Path(m.path).name.lower() for m in proc.memory_maps()
            ):
                loaded.append(proc.pid)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            inaccessible += 1
    ram = psutil.virtual_memory().available / 2**30
    disk = shutil.disk_usage(ROOT).free / 2**30
    return dict(available_ram_gib=round(ram, 2), available_disk_gib=round(disk, 2),
                other_gurobi_process_count=len(loaded), inaccessible_python_count=inaccessible,
                ready=ram >= 160 and disk >= 105 and not loaded and not inaccessible)


def refuse_solve(source, path, *, row_labels=None):
    tree = ast.parse(source, filename=path)
    banned = {'optimize', 'optimizeAsync', 'optimizeBatch'}
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
           n.func.attr in banned for n in ast.walk(tree)):
        raise ValueError('Optimization call in diagnostic prefix: ' + path)
    for name, label in (row_labels or {}).items():
        matches = [node.value for node in tree.body if isinstance(node, ast.Assign) and
                   any(isinstance(t, ast.Name) and t.id == name for t in node.targets)]
        if (len(matches) != 1 or not isinstance(matches[0], ast.Call)
                or not isinstance(matches[0].func, ast.Attribute)
                or matches[0].func.attr != 'addConstrs' or matches[0].keywords):
            raise ValueError('Unexpected unlabeled native row group: ' + name)
        matches[0].keywords.append(ast.keyword(arg='name', value=ast.Constant(label)))
    return compile(ast.fix_missing_locations(tree), path, 'exec')


def pinned_copy(source, target, expected, recorded):
    source = source.resolve()
    if ARCHIVE.resolve() not in source.parents or not source.is_file():
        raise ValueError('Invalid archive input: ' + str(source))
    digest = digest_file(source)
    if expected is not None and expected != digest:
        raise ValueError('Archived input hash mismatch: ' + str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if digest_file(target) != digest:
        raise ValueError('Copy verification failed: ' + str(target))
    recorded[target.relative_to(PROJECT).as_posix()] = {'source': str(source), 'sha256': digest}


def stage(label='avg', *, full_handoff=False):
    from openpyxl import load_workbook
    if label not in ('low', 'avg', 'high'):
        raise ValueError('Unknown electricity scenario')
    case = 'electricity_' + label
    scenario_dir = 'frozen_' + case
    task = json.loads((ARCHIVE / 'jobspecs.json').read_bytes())[case]
    donor = Path(task['project'])
    spec_path = Path(task['specification'])
    hashes = {Path(p): h for p, h in task['input_hashes'].items()}
    if digest_file(spec_path) != hashes[spec_path]:
        raise ValueError('Frozen specification mismatch')
    spec = json.loads(spec_path.read_bytes())
    paths = {}
    inputs = {}
    for entry in ('main_unmodified.py', 'config', 'data', 'scripts'):
        base = donor / entry
        files = [base] if base.is_file() else base.rglob('*')
        for path in files:
            if path.is_file() and '__pycache__' not in path.parts:
                pinned_copy(path, PROJECT / path.relative_to(donor), None, inputs)
    src_results = donor / 'results' / scenario_dir
    required = ('inv', 'transfer_new_cap', 'transfer_outside_new')
    for year in (2020, 2025):
        marker = json.loads((ARCHIVE / 'jobs' / case / str(year) /
                             'accepted.json').read_bytes())
        if full_handoff:
            archive = json.loads((ARCHIVE / 'preparation.json').read_bytes())
            if marker['fence'] != archive['fence'] or marker['case'] != case or marker['year'] != year:
                raise ValueError('Archived handoff failed its lineage check')
        folder = src_results / str(year)
        marker_hashes = {Path(p).name: h for p, h in marker['files'].items()}
        for path in folder.glob('*.csv'):
            if (full_handoff and path.name in marker_hashes
                    or any(path.name.startswith(prefix) for prefix in required)):
                pinned_copy(path, PROJECT / path.relative_to(donor), marker_hashes[path.name], inputs)
        for prefix in required:
            if not (PROJECT / 'results' / scenario_dir / str(year) /
                    f'{prefix}_{year}.csv').is_file():
                raise ValueError(f'Missing archive handoff {prefix} {year}')
    for field in ('ncre_path', 'genco_path', 'inflows_path', 'policy_path', 'summary_path'):
        source = Path(spec[field])
        target = PROJECT / 'diagnostic_inputs' / source.name
        pinned_copy(source, target, hashes[source], inputs)
        paths[field] = target
    summary = json.loads(paths['summary_path'].read_bytes())
    profile = Path(summary['climate_profile_path'])
    paths['profile_path'] = PROJECT / 'diagnostic_inputs' / profile.name
    pinned_copy(profile, paths['profile_path'], hashes[profile], inputs)
    summary['climate_profile_path'] = str(paths['profile_path'])
    summary['solver_last_hour'] = 8760
    paths['summary_path'].write_text(json.dumps(summary, indent=2), encoding='utf8')
    paths['genco_path'].write_bytes(electricity_candidate(paths['genco_path'].read_bytes()))
    policy = load_workbook(paths['policy_path'])
    setup = policy['setup']
    header = [cell.value for cell in setup[1]]
    col = header.index('last_hour') + 1
    count = 0
    for idx in range(2, setup.max_row + 1):
        cell = setup.cell(idx, col)
        if cell.value is not None:
            if int(cell.value) not in (8736, 8760):
                raise ValueError('Unexpected archived last hour')
            cell.value = 8760
            count += 1
    if not count:
        raise ValueError('Policy has no full-year setup')
    policy.save(paths['policy_path'])
    for p in paths.values():
        inputs[p.relative_to(PROJECT).as_posix()]['staged_sha256'] = digest_file(p)
    (ROOT / 'input_manifest.json').write_text(json.dumps(inputs, indent=2))
    return paths, inputs, spec['scenario'], summary['year_specific_curves']


def check_matrix(ctx, paths, inputs):
    model = ctx['ucp']
    model.update()
    zones = set(ctx['active_zones_system_df_list'])
    if zones != {('HR1', 'HR'), ('SI1', 'SI'), ('BA1', 'BA')}:
        raise ValueError('Unexpected active electricity zones: ' + repr(zones))
    if any(ctx[f] for f in ('model_heating', 'model_cooling', 'model_transport_and_industry',
                            'model_efuel', 'include_secc_links')):
        raise ValueError('Non-electric modules active')
    weights = ctx['representative_period_weights']
    if (tuple(ctx['years']) != (2030,) or tuple(ctx['periods']) != HOURS or
            any(w != 1 for w in weights.values()) or ctx['ANNUAL_REPRESENTED_HOURS'] != 8760 or
            set(ctx['energy_balance']) != {(*z, p, 2030) for z, p in product(zones, HOURS)}):
        raise ValueError('Native matrix does not have every full-year balance')
    active = {}
    for field in NATIVE_INPUTS:
        path = (PROJECT / ctx[field]).resolve()
        item = inputs[path.relative_to(PROJECT).as_posix()]
        if digest_file(path) != item.get('staged_sha256', item['sha256']):
            raise ValueError('Active input hash mismatch: ' + field)
        active[field] = str(path.relative_to(PROJECT))
    if not getattr(ctx.get('last_climate_apply_result'), 'prepared_inputs', False):
        raise ValueError('Prepared spatial profile did not reach the model')
    audit_built_limits(ctx, model, (2030,), row_labels=CAPACITY_ROW_LABELS)
    cf = {}
    with paths['ncre_path'].open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        units = {name: (name, zone, country) for name, zone, country in ctx['capacity_generator']
                 if country == 'HR' and name in reader.fieldnames and
                 ('wind' in name.lower() or 'solar' in name.lower() or 'pv' in name.lower())}
        if len(units) != 8:
            raise ValueError('Expected five wind and three solar unit profiles: ' + repr(units))
        for unit in units:
            cf[unit] = dict(hours=0, matrix_rows=0, min=1., max=0., max_error=0.)
        cap_exprs = {unit: ctx['available_capacity_year'][(*key, 2030)] for unit, key in units.items()}
        def terms(expr):
            result = {}
            for i in range(expr.size()):
                idx = expr.getVar(i).index
                result[idx] = result.get(idx, 0.) + expr.getCoeff(i)
            return {idx: coeff for idx, coeff in result.items() if coeff != 0.}
        cap_terms = {u: terms(e) for u, e in cap_exprs.items()}
        seen = set()
        for input_row in reader:
            if 'year' in input_row and int(input_row['year']) != 2030:
                continue
            p = int(input_row['period'])
            if p in seen:
                raise ValueError('Duplicate spatial profile hour')
            seen.add(p)
            for unit in units:
                value = float(input_row[unit])
                table = ctx['aval_factor_plant']
                key = (unit, p, 2030) if (unit, p, 2030) in table else (unit, p)
                actual = float(table[key])
                entry = cf[unit]
                entry['hours'] += 1
                entry['min'] = min(entry['min'], actual)
                entry['max'] = max(entry['max'], actual)
                entry['max_error'] = max(entry['max_error'], abs(actual - value))
                if not (0. <= actual <= 1. and isfinite(value)) or abs(actual - value) > 1e-8:
                    raise ValueError(f'Spatial factor mismatch: {unit}/{p}/{value}/{actual}')
                idx = (*units[unit], p, 2030)
                gen = ctx['generation'][idx]
                column = model.getCol(gen)
                matches = [column.getConstr(i) for i in range(column.size())
                           if column.getConstr(i).ConstrName.startswith('max_output_nondisp[')]
                if len(matches) != 1:
                    raise ValueError(f'Expected one built spatial generation row: {unit}/{p}')
                matrix_row, = matches
                expected = {i: -actual * c for i, c in cap_terms[unit].items()} if actual else {}
                expected[gen.index] = 1.
                observed = terms(model.getRow(matrix_row))
                if (matrix_row.Sense != '='
                        or abs(matrix_row.RHS - actual * cap_exprs[unit].getConstant()) > 1e-7
                        or any(abs(expected.get(i, 0.) - observed.get(i, 0.)) > 1e-9
                               for i in expected.keys() | observed.keys())):
                    raise ValueError(f'Built generation row differs from spatial factor: {unit}/{p}')
                entry['matrix_rows'] += 1
        if seen != set(HOURS):
            raise ValueError('Spatial inputs missing hours')
    imp, exp = ctx['import_outside'], ctx['export_outside']
    if not imp or imp.keys() != exp.keys():
        raise ValueError('Unmatched outside directions')
    pairs = [(float(imp[k].Obj), float(exp[k].Obj)) for k in imp]
    spreads = [a + b for a, b in pairs]
    if not all(isfinite(x) for x in spreads) or min(spreads) < -1e-12:
        raise ValueError('Outside tariff permits profitable same-hour round trips')
    return dict(year=2030, hours=8760, zone_balances=len(ctx['energy_balance']),
                zones=sorted(zones), operating_multiplier=float(ctx['multip']),
                variables=model.NumVars, constraints=model.NumConstrs,
                nonzeros=model.NumNZs, fingerprint=model.Fingerprint,
                active_native_inputs=active, spatial_cf=cf, krsko_mw=696,
                exchange=ctx['eraa_2030_build_audit'],
                prices=dict(pairs=len(pairs), min_spread=min(spreads),
                            negative_buy_coefficients=sum(a < 0 for a, _ in pairs),
                            import_objective_coefficients=[min(a for a, _ in pairs), max(a for a, _ in pairs)],
                            export_objective_coefficients=[min(b for _, b in pairs), max(b for _, b in pairs)]))


def build():
    resources = resource_check()
    save(state='resource_preflight', resources=resources)
    if not resources['ready']:
        return
    paths, inputs, scenario, year_specific = stage()
    ctx = assemble(paths, scenario, year_specific)
    if ctx is None:
        return
    save(state='matrix_verified_no_optimize', matrix=check_matrix(ctx, paths, inputs),
         source_status='archived_diagnostic_donor_only')
    ctx['ucp'].dispose()


def assemble(paths, scenario, year_specific, label='avg', fraction=1., matrix_only=True):
    os.chdir(PROJECT)
    sys.path[:0] = [str(PROJECT), str(REMOTE / 'energy_rebuild_20261019/runtime_packages')]
    for key in list(os.environ):
        if key.startswith(('H2RES_', 'LEGACY_')):
            del os.environ[key]
    for field, key in {'ncre_path': 'AVL_FACTOR', 'genco_path': 'GENCO', 'inflows_path': 'INFLOWS',
                       'policy_path': 'POLICY', 'summary_path': 'CLIMATE_SUMMARY',
                       'profile_path': 'CLIMATE_PROFILE'}.items():
        os.environ['H2RES_' + key + '_DAT'] = str(paths[field])
    os.environ.update(H2RES_FLEX_TECH_DAT='./data/flex_tech_HR_2020_2050_sdewes_regular6.xlsx',
                      H2RES_SCENARIO_NAME='frozen_electricity_'+label,
                      H2RES_CLIMATE_ENABLED='1', H2RES_CLIMATE_PREPARED_INPUTS='1',
                      H2RES_CLIMATE_SCENARIO=scenario, H2RES_CLIMATE_COUNTRY='HR',
                      H2RES_CLIMATE_PLANNING_YEAR='2030', H2RES_STRICT_CLIMATE_INPUTS='1',
                      H2RES_SIMPLE_CURVES='0' if year_specific else '1',
                      H2RES_REPRESENTATIVE_CAPACITY_MODE='0', H2RES_FIXED_CAPACITY_MODE='0',
                      H2RES_ROLLING_DISPATCH_MODE='0', H2RES_TWO_STAGE_AUTO_MODE='0',
                      H2RES_MODEL_HEATING='0', H2RES_MODEL_COOLING='0',
                      H2RES_MODEL_TRANSPORT_AND_INDUSTRY='0', H2RES_MODEL_EFUEL='0',
                      H2RES_MODEL_H2_SUPPLY_DEMAND_BALANCE='0', H2RES_MODEL_OIL_GAS_PRODUCTS_SUPPLY='0',
                      H2RES_MODEL_CO2_POST_GEN_STORAGE='0',
                      H2RES_STAGE1_CAPACITY_RESERVE_MARGIN='0.20',
                      H2RES_STAGE1_CAPACITY_RESERVE_START_YEAR='2025')
    import gurobipy as gp

    class MatrixModel(gp.Model):
        def optimize(self, *args, **kwargs):
            raise RuntimeError('Optimization forbidden in matrix-only diagnostic')

        optimizeAsync = optimizeBatch = optimize

    if matrix_only:
        gp.Model = MatrixModel
    main = PROJECT / 'main_unmodified.py'
    source = main.read_text(encoding='utf-8-sig')
    fence = '\n_configure_rolling_dispatch_granularity()'
    if source.count(fence) != 1:
        raise ValueError('Unexpected native entry fence')
    ctx = {'__file__': str(main), '__name__': '__main__'}
    exec(refuse_solve(source.split(fence)[0], str(main)), ctx)
    ctx['_configure_rolling_dispatch_granularity']()
    ctx['_reload_data_for_current_mode']()
    if not ctx['Myopic'] or tuple(ctx['periods']) != HOURS:
        raise ValueError('Archived configuration did not resolve to full-year Myopic')
    ctx.update(years=[2030], previous_years_set={2020, 2025}, any_chunk_solution=False)
    resources = resource_check()
    save(state='prepared_before_model_creation', resources=resources)
    if not resources['ready']:
        return
    scripts = PROJECT / 'scripts'
    var_path = scripts / 'reading_prev_and_vars_def_dual_industry_categories1.py'
    exec(refuse_solve(var_path.read_text(encoding='utf-8-sig'), str(var_path)), ctx)
    hourly = load_envelope((ROOT / 'candidate_hourly_mw.csv').read_bytes(),
                           (ROOT / 'preflight.json').read_bytes())
    ctx['_eraa_2030_fixed'] = apply_fixed_native_transfer_inputs(ctx, hourly), hourly, fraction
    matrix_path = scripts / 'Build_model_dual_industry_categories_named_debug.py'
    exec(refuse_solve(matrix_path.read_text(encoding='utf-8-sig'), str(matrix_path),
                      row_labels=CAPACITY_ROW_LABELS), ctx)
    finish_eraa_2030(ctx)
    objective = scripts / 'CAPEX_OPEX_Gurobi_settings_dual_industry_categories1.py'
    source = objective.read_text(encoding='utf-8-sig')
    if source.count(SET_OBJECTIVE) != 1:
        raise ValueError('Unexpected native objective anchor')
    source = render_signed_outside_prices(source).split(SET_OBJECTIVE)[0] + SET_OBJECTIVE
    exec(refuse_solve(source, str(objective)), ctx)
    return ctx


def main():
    if (os.name != 'nt' or ROOT.parent != REMOTE.resolve() or
            not ROOT.name.startswith('electricity_matrix_2030_') or
            socket.gethostname().lower() != EXPECTED_HOST):
        raise RuntimeError('Unexpected matrix diagnostic host or path')
    if '--worker' in sys.argv:
        try:
            build()
        except BaseException:
            save(state='failed', error=traceback.format_exc())
            raise
        return
    with (ROOT / 'launch.lock').open('x'):
        pass
    env = os.environ.copy()
    env.update(TMP=str(ROOT), TEMP=str(ROOT), PYTHONDONTWRITEBYTECODE='1', MPLCONFIGDIR=str(ROOT))
    with (ROOT / 'build.log').open('x') as log:
        proc = subprocess.Popen([sys.executable, '-B', '-u', __file__, '--worker'], cwd=ROOT,
                                env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    import psutil
    (ROOT / 'launch.json').write_text(json.dumps(
        dict(pid=proc.pid, created=psutil.Process(proc.pid).create_time(), script=__file__)))


if __name__ == '__main__':
    main()

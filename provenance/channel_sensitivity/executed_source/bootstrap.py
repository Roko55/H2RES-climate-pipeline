"""Prepare an isolated native planning batch with stationary weather profiles."""
from pathlib import Path
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
PREVIOUS = Path('E:/Roko/remote_access/channel_investments_20261101')
EXPECTED = Path('E:/Roko/remote_access/channel_sensitivity_20261102')
NEUTRAL = {'solar_factor_2050': 1.0, 'wind_factor_2050': 1.0, 'temp_offset_2050': 0.0}
INSPECTION_HASHES = {
    'inspect_status.py': 'e281357be7e5901a883ed12c39d1cbb1eb04a26daace1140e0a73f0d53709a0e',
    'cpu_inspection.py': '5f4ec2d8e2fc9a3d5dc72b6b7c8a5a82fc01355b605f10f2512d2f964926043d'}


def replace_once(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)


def neutral_source(text):
    tree = ast.parse(text)
    changed = []

    class Neutral(ast.NodeTransformer):
        def visit_Dict(self, node):
            self.generic_visit(node)
            for i, key in enumerate(node.keys):
                if isinstance(key, ast.Constant) and key.value in NEUTRAL:
                    changed.append((node.lineno, key.value))
                    node.values[i] = ast.Constant(NEUTRAL[key.value])
            return node

        def visit_keyword(self, node):
            self.generic_visit(node)
            if node.arg in NEUTRAL:
                changed.append((node.lineno, node.arg))
                node.value = ast.Constant(NEUTRAL[node.arg])
            return node

        def visit_Assign(self, node):
            self.generic_visit(node)
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                    if target.slice.value in NEUTRAL:
                        changed.append((node.lineno, target.slice.value))
                        node.value = ast.Constant(NEUTRAL[target.slice.value])
            return node

    tree = ast.fix_missing_locations(Neutral().visit(tree))
    return (ast.unparse(tree) + '\n' if changed else text), changed


def bootstrap():
    assert os.name == 'nt' and ROOT == EXPECTED
    assert ROOT.resolve().is_relative_to(Path('E:/Roko').resolve())
    sys.path.insert(0, 'E:/Roko/remote_access/energy_rebuild_20261019/runtime_packages')
    import psutil
    if (ROOT / 'preparation.json').exists():
        sys.path.insert(0, str(ROOT))
        from common import verify_sources
        verify_sources()
        return
    assert not (ROOT / 'controller_identity.json').exists()
    old_status = json.loads((PREVIOUS / 'status.json').read_text())
    assert old_status['status'] == 'completed_verified' and not old_status['active']
    for process in psutil.process_iter(['pid', 'cmdline']):
        cmd = process.info['cmdline'] or []
        assert not any(str(a).replace('\\', '/').lower() == (ROOT / 'worker.py').as_posix().lower()
                       for a in cmd), 'Existing worker in new batch'
    assert psutil.virtual_memory().available / 2**30 > 228
    assert shutil.disk_usage(ROOT).free / 2**30 > 100
    names = ['common.py', 'prepare.py', 'build_worker.py', 'hooks.py', 'worker.py',
             'controller.py', 'collect.py', 'session_policy.py', 'session_guard.py',
             'session_controller.py', 'continuation_checks.py', 'inspect_status.py',
             'cpu_inspection.py', 'launch.py']
    old_manifest = json.loads((PREVIOUS / 'session_recovery_manifest.json').read_text())
    old_prep = json.loads((PREVIOUS / 'preparation.json').read_text())
    source_proofs = []
    for name in names:
        raw = (PREVIOUS / name).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        expected = old_manifest['overlay_files'].get(name, old_prep['frozen_files'].get(name))
        if expected is None:
            expected = INSPECTION_HASHES.get(name)
        assert expected == digest, ('Unverified source', name)
        text = raw.decode('utf8').replace('E:/Roko/remote_access/channel_investments_20261101', ROOT.as_posix())
        (ROOT / name).write_text(text, encoding='utf8')
        source_proofs.append({'file': name, 'source_sha256': digest})
    sys.path.insert(0, str(ROOT))
    from common import save, load, sha, now, YEARS, DONORS
    for year in YEARS:
        folder = ROOT / 'reference' / str(year)
        folder.mkdir(parents=True, exist_ok=True)
        original = PREVIOUS / 'reference' / str(year) / 'prebuild_validation.json'
        shutil.copy2(original, folder / original.name)
        save(folder / 'reference_audit.json', {
            'year': year, 'reuse_proved': False, 'new_solve_started': False,
            'reason': 'All seven stationary-weather paths require fresh native optimization',
            'scope': 'Earlier non-climate signatures only; no old weather, thermal output or capacity checkpoint reused',
            'non_climate_signature_source_sha256': sha(original)})
    plan = load(PREVIOUS / 'plan.json')
    assert {v['id']: (v['solar_member'], v['wind_member'], v['temperature_member'])
            for v in plan['cases']} == DONORS
    plan.update(status='isolated_preflight', created=now(), climate_adjustments=NEUTRAL,
                required_new_planning_windows=49)
    plan['source']['selection_rule'] = 'Retain the previously accepted channel donor IDs; no reselection or retraining.'
    plan['reuse'] = {'enabled': False, 'reason': 'Weather changes in future slices of the first rolling window too.'}
    plan['completion'] = '49 newly optimized, independently verified native windows; no reused planning checkpoints.'
    plan['parallel_execution']['initial'] = 'Two licensed paths, four solver threads each; sequential native years within each path.'
    plan['parallel_execution']['target'] = 'At most two owned active/reserved sessions; shared provider session decisions are honored.'
    plan['design']['weather'] = 'Same selected 8760-hour synthetic profile at every planning year, without additional climate trends.'
    save(ROOT / 'plan.json', plan)

    path = ROOT / 'prepare.py'
    text = path.read_text()
    text = replace_once(text, 'from common import R, OLD, YEARS, CASES, DONORS, remote_only, sha, save, load, digest, now',
                        'from common import R, OLD, YEARS, CASES, DONORS, remote_only, sha, save, load, digest, now\nfrom bootstrap import neutral_source')
    text = replace_once(text, "        manager_path = target / 'scripts/scenario_manager.py'", """        for code_path in target.rglob('*.py'):
            if '__pycache__' in code_path.parts:
                continue
            content = code_path.read_text(encoding='utf8')
            neutral, edits = neutral_source(content)
            if edits:
                assert code_path.name != 'Build_model_RH.py'
                code_path.write_text(neutral, encoding='utf8')
        manager_path = target / 'scripts/scenario_manager.py'""")
    text = replace_once(text, "original = (source / 'scripts/scenario_manager.py').read_text(encoding='utf8')",
                        "original = manager_path.read_text(encoding='utf8')")
    text = text.replace('solar_factor_2050=1.03, wind_factor_2050=.92, temp_offset_2050=1.5',
                        'solar_factor_2050=1.0, wind_factor_2050=1.0, temp_offset_2050=0.0')
    text = replace_once(text, "model = ast.parse((source / 'scripts/modeling_approaches.py').read_text(encoding='utf8'))",
                        "model = ast.parse(model_path.read_text(encoding='utf8'))")
    text = replace_once(text, "for folder in ('projects', 'baseline', 'sources'):",
                        "for folder in ('projects', 'baseline', 'sources', 'reference'):")
    text = replace_once(text, "            base = manager._channel_profile('average', year)", """            base = manager._channel_profile('average', year)
            raw = np.column_stack([a[m, :, c] for c, m in enumerate(DONORS[case])])
            assert np.array_equal(profile, raw), (case, year, 'Weather trend remains')
            assert np.array_equal(manager._apply_climate_scaling(raw, year), raw)
            assert all(manager.CLIMATE_CHANGE[k] == v for k, v in
                       {'solar_factor_2050': 1., 'wind_factor_2050': 1., 'temp_offset_2050': 0.}.items())""")
    path.write_text(text, encoding='utf8')

    path = ROOT / 'hooks.py'
    text = path.read_text()
    text = replace_once(text, "            assert digest(actual) == ref['thermal_hashes'][name], 'Unchanged temperature channel changed'", """            for yy in ctx['years']:
                assert np.array_equal(manager._channel_profile(case, yy)[:, 2],
                                      manager._channel_profile('average', yy)[:, 2])""")
    text = replace_once(text, "        assert np.array_equal(profile, manager._channel_profile(case, yy))", """        assert np.array_equal(profile, manager._channel_profile(case, yy))
        raw = np.column_stack([manager._channel_ensemble[m, :, c]
                               for c, m in enumerate(DONORS[case])])
        assert np.array_equal(profile, raw), 'Executed profile differs from the stationary frozen member'
        assert all(manager.CLIMATE_CHANGE[k] == v for k, v in
                   {'solar_factor_2050': 1., 'wind_factor_2050': 1., 'temp_offset_2050': 0.}.items())""")
    path.write_text(text, encoding='utf8')
    path = ROOT / 'launch.py'
    path.write_text(path.read_text().replace("R / 'guard.py'", "R / 'session_guard.py'"), encoding='utf8')
    # Reference signatures remain observational; their weather/thermal hashes are not new evidence.
    path = ROOT / 'collect.py'
    text = path.read_text()
    text = text.replace("'scope': 'Same full multisector model; one climate channel changed at a time'",
                        "'scope': 'Same full multisector model; one weather channel changed at a time; no additional long-term climate trends'")
    path.write_text(text, encoding='utf8')
    for path in ROOT.glob('*.py'):
        compile(path.read_text(encoding='utf8'), str(path), 'exec')
    overlay_names = ['session_policy.py', 'session_guard.py', 'session_controller.py',
                     'continuation_checks.py', 'inspect_status.py', 'cpu_inspection.py',
                     'bootstrap.py', 'launch.py']
    save(ROOT / 'session_recovery_manifest.json', {
        'utc': now(), 'scope': 'Isolated stationary-weather execution',
        'overlay_files': {n: sha(ROOT / n) for n in overlay_names}})
    from prepare import prepare
    prepare()
    from continuation_checks import prove
    prove()
    save(ROOT / 'preflight.json', {
        'utc': now(), 'passed': True, 'source_proofs': source_proofs,
        'prior_results_preserved': True, 'solver_started': False,
        'fresh_paths': 7, 'fresh_windows': 49, 'no_checkpoint_reuse': True,
        'climate_adjustments': NEUTRAL, 'full_hourly_profile_checks': 49,
        'mapping_checks': 98, 'available_GiB': psutil.virtual_memory().available / 2**30,
        'cpu_percent': psutil.cpu_percent(interval=2), 'free_disk_GiB': shutil.disk_usage(ROOT).free / 2**30,
        'initial_state_source': str(ROOT / 'baseline/legacy_whole_path_03/mapping_20261030/results/repaired_avg/quality/inherited_2020.pkl'),
        'dependent_results': {
            'generator_validation': 'Unchanged generator and ensemble; no recomputation required',
            'previous_trend_results': 'Retained as their own experiment; not relabelled as stationary weather',
            'historical_portfolio_tests': 'Existing tests retain their original portfolios; do not claim they validate these new portfolios',
            'manuscript': 'Not modified; match numerical claims to the new manifest after completion'}})


if __name__ == '__main__':
    try:
        bootstrap()
    except BaseException:
        (ROOT / 'bootstrap_error.json').write_text(json.dumps({
            'utc': datetime.now(timezone.utc).isoformat(),
            'traceback': __import__('traceback').format_exc()}, indent=2), encoding='utf8')
        raise

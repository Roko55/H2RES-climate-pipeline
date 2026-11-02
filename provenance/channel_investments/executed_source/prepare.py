"""Freeze the accepted model, independent channel cases and their provenance."""
from pathlib import Path
import ast
import csv
import importlib.util
import json
import os
import shutil
import sys
from common import R, OLD, YEARS, CASES, DONORS, remote_only, sha, save, load, digest, now

WRAPPER = '''
# Fixed independent-channel sensitivity inputs; selection is frozen before optimization.
_channel_base_get = get_representative_scenarios
_channel_members = {donors}
_channel_ensemble = np.load(os.path.join(DATA_DIR, 'xgb_synthetic_scenarios.npy'), allow_pickle=False)
def _channel_profile(case, year):
    members = _channel_members[case]
    profile = np.column_stack([_channel_ensemble[m, :, c] for c, m in enumerate(members)])
    return _apply_climate_scaling(profile, 2020 if year is None else int(year))
def get_representative_scenarios(year=None):
    case = os.environ.get('CHANNEL_CASE', 'average')
    profile = _channel_profile(case, year)
    if case == 'average':
        records = _channel_base_get(year)
        assert np.array_equal(records['avg_yield__normal']['profile'], profile)
        return records
    return {{case + '__channel': {{'profile': profile, 'probability': 1.0,
             'total_energy': float(profile[:, :2].sum()), 'donor_members': _channel_members[case],
             'weight_meaning': 'one deterministic sensitivity case, not an ensemble cluster share'}}}}
'''


def prepare():
    remote_only()
    if (R / 'preparation.json').exists():
        from common import verify_sources
        return verify_sources()
    sys.path.insert(0, 'E:/Roko/remote_access/energy_rebuild_20261019/runtime_packages')
    import numpy as np
    import psutil
    from sklearn.cluster import KMeans
    assert shutil.disk_usage(R).free / 2**30 > 100
    save(R / 'preparation_progress.json', {'utc': now(), 'stage': 'copy_and_verify_sources',
         'available_GiB': psutil.virtual_memory().available / 2**30})
    plan = load(R / 'plan.json')
    ensemble = OLD / 'profiles/synthetic_scenarios.npy'
    assert sha(ensemble) == plan['source']['ensemble_sha256']
    source = OLD / 'multisector/project'
    private = Path('E:/Roko/remote_access/resource_batch_20261029/legacy_whole_path_03/mapping_20261030')
    copies = {
        OLD / 'multisector/full_worker.py': R / 'sources/full_worker.py',
        OLD / 'multisector/audited_export_helpers.py': R / 'audited_export_helpers.py',
        OLD / 'multisector/post_audit.py': R / 'sources/post_audit.py',
        source / 'scripts/Build_model_RH.py': R / 'sources/Build_model_RH.py',
        private / 'projects/repaired_avg/review_command.json': R / 'baseline/legacy_whole_path_03/mapping_20261030/projects/repaired_avg/review_command.json',
        private / 'results/repaired_avg/legacy_runner.py': R / 'baseline/legacy_whole_path_03/mapping_20261030/results/repaired_avg/legacy_runner.py',
        private / 'results/repaired_avg/quality/inherited_2020.pkl': R / 'baseline/legacy_whole_path_03/mapping_20261030/results/repaired_avg/quality/inherited_2020.pkl',
        private.parents[1] / 'helpers/precision_policy.py': R / 'baseline/helpers/precision_policy.py'}
    provenance = []
    for src, dst in copies.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        assert sha(src) == sha(dst)
        provenance.append({'source': str(src), 'copy': str(dst), 'sha256': sha(src)})
    assert sha(R / 'sources/Build_model_RH.py') == '53babcaf17a4e2a3411b8f9f3d22a771dc3b3b901531f01ab2028397e8bd7456'
    a = np.load(ensemble, allow_pickle=False)
    assert a.shape == (100, 8760, 3) and np.isfinite(a).all()
    assert np.all((a[:, :, :2] >= 0) & (a[:, :, :2] <= 1))
    means = a.astype(float).mean(axis=1)
    def representatives(values):
        km = KMeans(n_clusters=3, random_state=42, n_init=10).fit(values[:, None])
        result = []
        for label in np.argsort(km.cluster_centers_[:, 0]):
            ids = np.flatnonzero(km.labels_ == label)
            result.append((int(ids[np.argmin(abs(values[ids] - values[ids].mean()))]), ids.tolist()))
        assert 65 in result[1][1]
        return result
    s, w = representatives(means[:, 0]), representatives(means[:, 1])
    t = a[:, :, 2].astype(float)
    hd, cd = np.maximum(18-t, 0).sum(axis=1), np.maximum(t-22, 0).sum(axis=1)
    tt = representatives((cd-cd.mean())/cd.std() - (hd-hd.mean())/hd.std())
    assert [s[0][0], s[2][0], w[0][0], w[2][0], tt[0][0], tt[2][0]] == [2, 28, 20, 72, 41, 48]
    rows, mapping = [], []
    reference_profiles = {}
    for case in CASES:
        target = R / 'projects' / case
        shutil.copytree(source, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('results', '__pycache__', '.git', '*.log', '*.pyc', 'quality'))
        assert sha(target / 'scripts/Build_model_RH.py') == sha(R / 'sources/Build_model_RH.py')
        data = target / 'data/SC/xgb_synthetic_scenarios.npy'
        assert sha(data) == plan['source']['ensemble_sha256']
        manager_path = target / 'scripts/scenario_manager.py'
        original = (source / 'scripts/scenario_manager.py').read_text(encoding='utf8')
        manager_path.write_text(original + WRAPPER.format(donors=repr(DONORS)), encoding='utf8')
        model_path = target / 'scripts/modeling_approaches.py'
        model = ast.parse((source / 'scripts/modeling_approaches.py').read_text(encoding='utf8'))
        count = 0
        for node in ast.walk(model):
            if not isinstance(node, ast.For):
                continue
            for i, statement in list(enumerate(node.body)):
                if isinstance(statement, ast.Expr) and ast.unparse(statement) == "exec(open('scripts/Build_model_RH.py').read())":
                    node.body.insert(i, ast.parse('_channel_prebuild(globals())').body[0])
                    count += 1
        assert count == 1
        model_path.write_text(ast.unparse(ast.fix_missing_locations(model)) + '\n', encoding='utf8')
        os.environ['CHANNEL_CASE'] = case
        spec = importlib.util.spec_from_file_location('mapping_' + case, manager_path)
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
        manager.CLIMATE_CHANGE.update(base_year=2020, target_year=2050,
                                     solar_factor_2050=1.03, wind_factor_2050=.92, temp_offset_2050=1.5)
        for year in YEARS:
            profile = manager._channel_profile(case, year)
            base = manager._channel_profile('average', year)
            changed = [c for c in range(3) if not np.array_equal(profile[:, c], base[:, c])]
            assert len(changed) == (0 if case == 'average' else 1)
            if case == 'average':
                records = manager.get_representative_scenarios(year)
                assert np.array_equal(records['avg_yield__normal']['profile'], base)
            unit_profiles, diag = manager.decompose_to_h2res_units(profile, return_diagnostics=True)
            for channel, capacities in [('solar', manager._get_solar_units('HR')),
                                        ('wind', manager._get_wind_units('HR'))]:
                aggregate = sum(unit_profiles[u] * c for u, c in capacities.items()) / sum(capacities.values())
                error = float(np.max(abs(aggregate - profile[:, 0 if channel == 'solar' else 1])))
                mapping.append({'case': case, 'year': year, 'channel': channel, 'max_error': error})
                assert error < 1e-12, ('mapping_conservation', case, year, channel, error)
            rows.append({'case': case, 'year': year, 'solar_member': DONORS[case][0],
                         'wind_member': DONORS[case][1], 'temperature_member': DONORS[case][2],
                         'mean_solar_CF': float(profile[:, 0].astype(float).mean()),
                         'mean_wind_CF': float(profile[:, 1].astype(float).mean()),
                         'mean_temperature_C': float(profile[:, 2].astype(float).mean()),
                         'heating_degree_hours_18C': float(np.maximum(18-profile[:, 2], 0).sum()),
                         'cooling_degree_hours_22C': float(np.maximum(profile[:, 2]-22, 0).sum()),
                         'profile_sha256': __import__('hashlib').sha256(profile.tobytes()).hexdigest()})
        save(R / 'preparation_progress.json', {'utc': now(), 'stage': 'mapping_verified', 'case': case})
    with (R / 'scenario_inputs.csv').open('w', encoding='utf8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    save(R / 'mapping_checks.json', mapping)
    from build_worker import build
    build()
    frozen = {}
    for folder in ('projects', 'baseline', 'sources'):
        for p in (R / folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:
                frozen[p.relative_to(R).as_posix()] = sha(p)
    for p in R.glob('*.py'):
        frozen[p.name] = sha(p)
    for name in ('plan.json', 'scenario_inputs.csv', 'mapping_checks.json'):
        frozen[name] = sha(R / name)
    preparation = {'utc': now(), 'execution_fence': digest(frozen), 'frozen_files': frozen,
                   'source_provenance': provenance, 'years': YEARS, 'cases': CASES,
                   'ensemble_sha256': sha(ensemble), 'native_equations_unchanged': True,
                   'new_training': False, 'reference_reuse': 'pending seven same-model reconstruction audits',
                   'threads_per_solver': 4, 'constraint_bound_tolerance': 1e-7,
                   'objective_closure_tolerance': .01, 'temperature_scale_annual_totals': False,
                   'hours_per_native_year': 8760, 'window_size': 2}
    save(R / 'preparation.json', preparation)
    return preparation


if __name__ == '__main__':
    prepare()

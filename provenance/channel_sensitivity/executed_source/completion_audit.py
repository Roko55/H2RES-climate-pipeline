"""Read-only verification of the completed stationary-weather planning batch."""
import ast
import csv
import hashlib
import json
import pickle
import sys
from common import R, YEARS, CASES, DONORS, remote_only, verify_sources, accepted, load, save, sha, now


def audit():
    remote_only()
    sys.path.insert(0, 'E:/Roko/remote_access/energy_rebuild_20261019/runtime_packages')
    import numpy as np
    from session_policy import verify_overlay
    prep = verify_sources()
    overlay = verify_overlay()
    status = load(R / 'status.json')
    assert status['status'] == 'completed_verified' and not status['active']
    assert status['accepted_new_windows'] == status['expected_new_windows'] == 49
    assert status['reused_reference_windows'] == 0 and not status['failures']
    assert sha(R / 'sources/Build_model_RH.py') == '53babcaf17a4e2a3411b8f9f3d22a771dc3b3b901531f01ab2028397e8bd7456'
    tree = ast.parse((R / 'audited_export_helpers.py').read_text(encoding='utf8'))
    definitions = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                   and n.name in ('clean', 'digest', 'DataOnly')]
    assert len(definitions) == 3
    ns = {'np': np, 'pickle': pickle, 'hashlib': hashlib, 'json': json}
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
                 'checkpoint_data_reader', 'exec'), ns)

    def state_hash(path):
        with path.open('rb') as stream:
            return ns['digest'](ns['DataOnly'](stream).load()['state'])

    initial = state_hash(R / 'baseline/legacy_whole_path_03/mapping_20261030/results/repaired_avg/quality/inherited_2020.pkl')
    profiles = list(csv.DictReader((R / 'scenario_inputs.csv').open(encoding='utf8')))
    assert len(profiles) == 49
    expected = {(row['case'], int(row['year'])): row for row in profiles}
    actual, checks = {}, []
    for case in CASES:
        previous = initial
        case_hashes = set()
        for year in YEARS:
            assert accepted(case, year, verify=True)
            out = R / 'jobs' / case / str(year)
            pre = load(out / 'prebuild_validation.json')
            inputs = load(out / 'input_audit.json')
            solver = load(out / 'solver_result.json')
            assert pre['inherited_state_sha256'] == inputs['inherited_state_sha256'] == previous
            assert pre['window_years'] == solver['window_years']
            row = expected[case, year]
            assert tuple(int(row[k]) for k in ('solar_member', 'wind_member', 'temperature_member')) == DONORS[case]
            case_hashes.add(row['profile_sha256'])
            for mapping in pre['mapping']:
                assert mapping['input_profile_sha256'] == expected[case, int(mapping['year'])]['profile_sha256']
                assert max(mapping['max_errors'].values()) < 1e-12
            check = load(R / 'records' / (case + '_' + str(year) + '_input_check.json'))
            assert check['exogenous_inputs_match'] and not any(check['differences'].values())
            ledger = load(out / 'annual_objective_ledger.json')
            ledger_gap = sum(ledger['values_by_anchor'].values()) + ledger['constant'] + ledger['unassigned'] - solver['objective']
            components = list(csv.DictReader((out / 'objective_breakdown_outputs.csv').open(encoding='utf8')))
            component_gap = sum(float(v['value']) for v in components if int(v['active_in_objective'])) - solver['objective']
            assert abs(ledger_gap) < .01 and abs(component_gap) < .01
            previous = state_hash(out / 'after_window.pkl')
            actual[case, year] = pre
            checks.append({'case': case, 'year': year, 'status': solver['status'],
                           'constraint_violation': solver['constraint_violation'],
                           'bound_violation': solver['bound_violation'], 'ledger_gap': ledger_gap,
                           'component_gap': component_gap, 'state_handoff_verified': True})
        assert len(case_hashes) == 1, 'Stationary profiles changed with the planning year'
    for year in YEARS:
        reference = actual['average', year]
        for case in CASES:
            pre = actual[case, year]
            assert pre['common_input_hashes'] == reference['common_input_hashes']
            assert pre['other_availability_sha256'] == reference['other_availability_sha256']
            if not case.startswith('temperature_'):
                assert pre['thermal_hashes'] == reference['thermal_hashes']
    independent = load(R / 'post_audit.json')
    assert all(independent[k] for k in ('stock_all_passed', 'emissions_all_passed', 'balances_all_passed'))
    assert len(independent['balance_checks']) == 49
    final = load(R / 'final_integrity.json')
    assert final['all_passed'] and final['new_planning_windows'] == final['optimal'] == 49
    assert final['reused_windows'] == 0
    save(R / 'completion_audit.json', {
        'utc': now(), 'passed': True, 'windows_checked': 49,
        'frozen_source_files_verified': len(prep['frozen_files']),
        'overlay_files_verified': len(overlay['overlay_files']), 'checks': checks,
        'stationary_weather_hashes_verified': True, 'unchanged_channels_verified': True,
        'native_equations_unchanged': True, 'own_complete_state_handoffs_verified': True,
        'stock_checks': len(independent['stock_checks']),
        'emission_checks': len(independent['emission_checks']), 'balance_checks': 49,
        'new_optimization_or_training': False, 'manuscript_updated': False})


if __name__ == '__main__':
    try:
        audit()
    except BaseException:
        save(R / 'completion_audit_error.json', {'utc': now(), 'traceback': __import__('traceback').format_exc()})
        raise

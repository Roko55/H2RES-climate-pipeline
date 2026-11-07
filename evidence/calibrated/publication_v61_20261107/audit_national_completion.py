"""Read-only audit of the national results and the realized capital chain."""

import argparse
import csv
from hashlib import sha256
import json
from math import fsum, isfinite
from pathlib import Path
import pickle

from electricity_project_integration import digest_file
from finalize_national_preflight import AUDITS, require_national_admission
from multisector_planning_integration import CASES, load_approved_base
from qualify_national_stock import read
from verify_planning_capital import HORIZON, TOL, resume_accepted_windows


class DataOnly(pickle.Unpickler):
    def find_class(self, module, name):
        import numpy as np
        if module == 'numpy' and name == 'dtype':
            return np.dtype
        if module in ('numpy.core.multiarray','numpy._core.multiarray') and name == 'scalar':
            return np.core.multiarray.scalar
        raise pickle.UnpicklingError(module+'.'+name)


def snapshot(path):
    with path.open('rb') as stream:
        return DataOnly(stream).load()


def verify_window(root, directory, years, case, plan, hashes):
    accepted = read(directory/'accepted.json')
    outputs = set(AUDITS) | {
        'solver_result.json','input_audit.json','objective_breakdown_outputs.csv','annual_objective_ledger.json',
        'after_window.pkl','runtime_accounting_inputs.csv.gz','all_variables.csv.gz','all_constraints.csv.gz',
        'keyed_constraints.csv.gz','keyed_outputs.csv.gz',f'inherited_{years[0]}.pkl','prebuild_validation.json',
        'investment_terms.csv','capital_handoff_audit.json','spatial_input_audit.json',
        'country_exchange_solution_audit.json','fixed_demand_solution_audit.json','stationary_storage_solution_audit.json',
        'hydro_solution_audit.json','hydrogen_solution_audit.json','district_heat_solution_audit.json',
        'power_policy_solution_audit.json','dispatchable_renewables_solution_audit.json',
    }
    if not outputs <= set(accepted['files']):
        raise ValueError('Native window is missing an audited result artifact: '+str(directory))
    pre = read(directory/'prebuild_validation.json')
    ref = read(root/'reference'/str(years[0])/'prebuild_validation.json')
    if (pre['case'] != case or pre['window_years'] != list(years)
            or pre['common_input_hashes'] != ref['common_input_hashes']
            or pre['other_availability_sha256'] != ref['other_availability_sha256']
            or (not case.startswith('temperature_') and pre['thermal_hashes'] != ref['thermal_hashes'])
            or [item['year'] for item in pre['mapping']] != list(years)):
        raise ValueError('Executed case does not match the common inputs: '+str(directory))
    for item in pre['mapping']:
        if (item['input_profile_sha256'] != hashes[case]
                or set(item['max_errors']) != {'wind','solar'}
                or any(not 0 <= v <= 1e-12 for v in item['max_errors'].values())):
            raise ValueError('Unexpected stationary profile or spatial mapping: '+str(directory))
    solver = read(directory/'solver_result.json')
    ledger = read(directory/'annual_objective_ledger.json')
    with (directory/'objective_breakdown_outputs.csv').open(encoding='utf8',newline='') as stream:
        active = fsum(float(r['value']) for r in csv.DictReader(stream) if int(r['active_in_objective']))
    gaps = (active-solver['objective'],fsum((*ledger['values_by_anchor'].values(),
                                             ledger['constant'],ledger['unassigned'],-solver['objective'])))
    audit = read(directory/'capital_handoff_audit.json')
    if (any(not isfinite(gap) or abs(gap) >= .01 for gap in gaps)
            or any(not 0 <= item['max_surviving_capacity_residual'] <= TOL for item in audit.values())
            or years[0] not in plan['production_anchors']):
        raise ValueError('Objective or capital accounting failed: '+str(directory))
    return dict(case=case, year=years[0], objective_model_units=solver['objective'],
                constraint_violation=solver['constraint_violation'],bound_violation=solver['bound_violation'],
                objective_gap=max(map(abs,gaps)),accepted_sha256=digest_file(directory/'accepted.json'))


def verify_recovery(root, original):
    updated = root/'recovery_03/status.json'
    repaired_hydro = updated.is_file() and read(updated)['status']=='planning_completed'
    folder = root/('recovery_03' if repaired_hydro else 'recovery_02')
    previous = root/('recovery_02' if repaired_hydro else 'recovery_01')
    recovery = read(folder/'status.json')
    before_path = folder/'original_production_status.json'
    if (recovery['status'] != 'planning_completed'
            or recovery['original_coordination_sha256'] != digest_file(root/'coordination/status.json')
            or recovery['original_status_sha256'] != digest_file(before_path)
            or recovery['original_controller_sha256'] != digest_file(root/'national_production_controller.py')
            or recovery['previous_recovery_status_sha256'] != digest_file(previous/'status.json')
            or recovery['original_status_sha256'] != digest_file(root/'recovery_01/original_production_status.json')
            or original['status'] != 'stopped'):
        raise ValueError('The national continuation lacks a complete recovery receipt')
    code = recovery['recovery_code_sha256']
    if (set(code) != {'national_production_recovery.py','national_window_continuation.py'}
            or any(digest_file(folder/name) != value for name,value in code.items())):
        raise ValueError('The national continuation code changed')
    before = read(before_path)
    if before['status'] != 'incomplete':
        raise ValueError('Unexpected original production status')
    prior = None
    if repaired_hydro:
        path = folder/'previous_production_status.json'
        if recovery['previous_production_status_sha256'] != digest_file(path):
            raise ValueError('The first resumed production prefix changed')
        prior = read(path)
        if prior['status'] != 'incomplete':
            raise ValueError('Unexpected previous production status')
    for case in CASES:
        for year in HORIZON[1:]:
            if year in before['completed'].get(case, []):
                continue
            source = previous if prior and year in prior['completed'].get(case, []) else folder
            check = read(source/'input_checks'/case/f'{year}.json')
            if ((check['case'],check['year']) != (case,year) or check['exogenous_inputs_match'] is not True
                    or any(check[name] for name in ('changed','extra','missing'))):
                raise ValueError(f'Unverified recovered exogenous inputs for {case}/{year}')
    return recovery


def audit(root, case_only=None):
    import numpy as np
    prep = require_national_admission(root)
    load_approved_base(root,prep['historical_base_sha256'])
    for relative,digest in prep['frozen_files'].items():
        if digest_file(root/relative) != digest:
            raise ValueError('Production input bytes changed: '+relative)
    plan = read(root/'plan.json')
    ensemble = np.load(root/'projects/average/data/SC/xgb_synthetic_scenarios.npy',allow_pickle=False)
    profiles = {r['id']: np.column_stack([ensemble[r[key],:,i] for i,key in enumerate(
        ('solar_member','wind_member','temperature_member'))]) for r in plan['cases']}
    if set(profiles) != set(CASES) or len(plan['cases']) != len(CASES):
        raise ValueError('Unexpected seven-case profile design')
    for case,profile in profiles.items():
        change = np.any(profile!=profiles['average'],axis=0).tolist()
        expected = [case.startswith(prefix) for prefix in ('solar_','wind_','temperature_')]
        if profile.shape != (8760,3) or not np.isfinite(profile).all() or change != expected:
            raise ValueError('Sensitivity case changes the wrong weather input: '+case)
    hashes = {case:sha256(profile.tobytes()).hexdigest() for case,profile in profiles.items()}
    if case_only:
        reached, _ = resume_accepted_windows(root/'jobs'/case_only, case_only,
                                            prep['execution_fence'], prep['historical_base_sha256'], snapshot)
        windows = [verify_window(root,root/'jobs'/case_only/str(HORIZON[i]),
                                 HORIZON[i:i+2],case_only,plan,hashes) for i in range(1,reached)]
        return dict(status='verified_prefix',case=case_only,windows=len(windows),evidence=windows)
    end = read(root/'coordination/status.json')
    if end['status'] == 'stopped':
        end = verify_recovery(root,end)
    status = read(root/'status.json')
    if (end['status'] != 'planning_completed' or end['planning_status_sha256'] != digest_file(root/'status.json')
            or status['status'] != 'completed_verified' or status['failed']
            or set(status['completed']) != set(CASES)):
        raise ValueError('The native national batch has not completed')
    reports = []
    for case in CASES:
        job = root/'jobs'/case
        reached, _ = resume_accepted_windows(job,case,prep['execution_fence'],prep['historical_base_sha256'],snapshot)
        if reached != len(HORIZON) or status['completed'][case] != list(HORIZON[1:]):
            raise ValueError('Incomplete or mismatched native capital chain: '+case)
        for i,year in enumerate(HORIZON[1:],1):
            reports.append(verify_window(root,job/str(year),HORIZON[i:i+2],case,plan,hashes))
    if len(reports) != 42 or plan['required_new_planning_windows'] != 42:
        raise ValueError('Unexpected number of verified national windows')
    return dict(status='verified',cases=len(CASES),windows=len(reports),evidence=reports)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root',type=Path)
    p.add_argument('--case',choices=CASES,help='Verify the accepted prefix while production is running')
    args = p.parse_args()
    print(json.dumps(audit(args.root.resolve(),args.case),indent=2,allow_nan=False))

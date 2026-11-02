"""Verify delivered assets and the final archived experiment evidence."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from scripts.release_runtime import accepted_array, solar_context, ENSEMBLE_SHA

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    manifest = json.loads((ROOT/'artifact_manifest.json').read_text())
    for name, record in manifest['files'].items():
        path = (ROOT/name).resolve()
        path.relative_to(ROOT)
        require(path.stat().st_size == record['bytes'] and digest(path) == record['sha256'],
                'Artifact mismatch: '+name)
    sources = json.loads((ROOT/'provenance/source_manifest.json').read_text())
    for name, record in sources.items():
        require(digest(ROOT/name) == record['sha256'], 'Source mismatch: '+name)
    metadata = json.loads((ROOT/'models/default/metadata.json').read_text())
    expected = {('Solar', q) for q in (.025, .1, .16, .5, .84, .9, .975)}
    expected |= {('Temperature', q) for q in (.16, .5, .84)}
    require(len(metadata['models']) == 10 and
            {(r['channel'], r['quantile']) for r in metadata['models']} == expected,
            'Incomplete final native model bundle')
    for row in metadata['models']:
        path = (ROOT/'models/default'/row['file']).resolve()
        path.relative_to((ROOT/'models/default').resolve())
        require(digest(path) == row['sha256'], 'Native model mismatch: '+row['file'])
    accepted_array(ROOT)
    solar_context(ROOT/'models/default/solar')
    require(metadata['ensemble_sha256'] == ENSEMBLE_SHA and
            not metadata['reporting_used_for_selection'], 'Invalid final cohort identity or selection boundary')
    final = json.loads((ROOT/'provenance/final/final_integrity.json').read_text())
    require(final['all_required_checks_passed'] and final['planning_windows'] == 63
            and final['operational_cases'] == 45 and final['verified_infeasible'] == 0
            and final['input_ensemble_sha256'] == ENSEMBLE_SHA, 'Final cohort integrity failed')
    mapping = json.loads((ROOT/'provenance/final/mapping_max_error_audit.json').read_text())
    require(mapping['passed'] and len(mapping['checks']) == 126 and
            all(row['passed'] and row['max_abs_error'] <= 1e-12 for row in mapping['checks']),
            'Archived spatial aggregate conservation failed')
    plan = pd.read_csv(ROOT/'results/planning_objectives.csv')
    require(len(plan) == 63 and not plan.duplicated(['configuration', 'case', 'year']).any(),
            'Planning table dimensions differ')
    require(np.isfinite(plan.objective).all() and (plan.constraint_violation <= 1e-7).all()
            and (plan.bound_violation <= 1e-7).all(), 'Planning solution quality failed')
    operations = pd.read_csv(ROOT/'results/operational_checks.csv')
    require(len(operations) == 45 and (operations.solver_status == 2).all()
            and (operations.status == 'verified_optimal').all()
            and (operations.HR_unserved_MWh.abs() <= 1e-6).all()
            and operations.verified_fixed_stock.all(), 'Operational evidence failed')
    counts = operations.groupby('availability_fraction').size().to_dict()
    require(counts == {0.: 15, .5: 15, 1.: 15}, 'Grid stress dimensions differ')
    require((operations.constraint_violation <= 1e-7).all()
            and (operations.bound_violation <= 1e-7).all(), 'Operational solution quality failed')
    metrics = pd.read_csv(ROOT/'results/profile_metrics.csv')
    require((metrics['mask'] == 'complete_reporting').all() and (metrics.n_hours == 43800).all()
            and not metrics.duplicated(['channel', 'method']).any(), 'Mixed reporting masks')
    from analysis.channel_results import verify_results
    channels = verify_results(ROOT)
    print(json.dumps({'status': 'passed', 'files_verified': len(manifest['files']),
        'native_models_verified': 10, 'ensemble_sha256': ENSEMBLE_SHA,
        'previous_planning_windows': len(plan), 'previous_operational_cases': len(operations),
        'single_channel_planning': channels,
        'scope': 'Artifact identities, solar runtime assets and archived solver evidence; optimizer not rerun.'}, indent=2))


if __name__ == '__main__':
    main()

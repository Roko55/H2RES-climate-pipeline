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
    operations = pd.read_csv(ROOT/'results/operational_checks.csv')
    require(len(operations) == 45 and (operations.solver_status == 2).all()
            and (operations.status == 'optimal_operation').all()
            and operations.verified_fixed_stock.all(), 'Operational evidence failed')
    counts = operations.groupby('availability_fraction').size().to_dict()
    require(counts == {0.: 15, .5: 15, 1.: 15}, 'Grid stress dimensions differ')
    require((operations.constraint_violation <= 1e-7).all()
            and (operations.bound_violation <= 1e-7).all(), 'Operational solution quality failed')
    require(operations.hours.eq(8760).all() and operations.max_storage_cycle_error.le(1e-7).all()
            and operations.storage_terminal_cycle_active.all()
            and set(operations.portfolio) == {'low', 'avg', 'high'}
            and operations.groupby('portfolio').portfolio_sha256.nunique().eq(1).all(),
            'Fixed full-year portfolio scope differs')
    zero_exchange = operations[operations.availability_fraction == 0]
    require((zero_exchange.HR_import_MWh.abs() <= 1e-6).all()
            and (zero_exchange.HR_export_MWh.abs() <= 1e-6).all(), 'Zero-exchange case contains trade')
    metrics = pd.read_csv(ROOT/'results/profile_metrics.csv')
    require((metrics['mask'] == 'complete_reporting').all() and (metrics.n_hours == 43800).all()
            and not metrics.duplicated(['channel', 'method']).any(), 'Mixed reporting masks')
    from analysis.channel_results import verify_results
    from analysis.manuscript_results import verify_manuscript
    from analysis.native_results import operational_table, evidence
    from analysis.zonal_results import audit as audit_zonal
    pd.testing.assert_frame_equal(operations, operational_table(ROOT), check_dtype=False, rtol=1e-12, atol=1e-8)
    zonal = audit_zonal(evidence(ROOT)/'zonal_2030_production_05')
    channels = verify_results(ROOT)
    print(json.dumps({'status': 'passed', 'files_verified': len(manifest['files']),
        'native_models_verified': 10, 'ensemble_sha256': ENSEMBLE_SHA,
        'fixed_portfolio_operational_cases': len(operations),
        'full_hourly_zonal_verification': {k:v for k,v in zonal.items() if k != 'evidence'},
        'single_channel_planning': channels,
        'manuscript': verify_manuscript(ROOT),
        'scope': 'Artifact identities, solar runtime assets and archived solver evidence; optimizer not rerun.'}, indent=2))


if __name__ == '__main__':
    main()

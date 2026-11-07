"""Reject held-out years in the resolved spatial inputs and their fitting lineage."""
from datetime import datetime, timezone
import json
from math import isclose
from pathlib import Path

from electricity_project_integration import digest_file


CUTOFF = datetime(2020,1,1,tzinfo=timezone.utc)


def parse_utc(value):
    timestamp = datetime.fromisoformat(value.replace('Z','+00:00'))
    if timestamp.utcoffset() != CUTOFF.utcoffset():
        raise ValueError('Spatial lineage must state the UTC boundary explicitly')
    return timestamp


def audit_resolved_spatial_paths(manager, project, frozen, contract_path, wind_mode, read_parquet=None,
                                 inventory=None, genco_path=None):
    """Check actual scenario-manager paths; do not permit legacy fallback ratios."""
    import pandas as pd

    root = Path(project).resolve()

    def verified(path):
        path = Path(path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Spatial input outside the isolated project or absent: '+str(path))
        key = path.relative_to(root).as_posix()
        if digest_file(path) != frozen.get(key):
            raise ValueError('Resolved spatial input does not match the frozen manifest: '+key)
        return path

    contract = json.loads(verified(contract_path).read_bytes())
    if (parse_utc(contract['held_out_from_utc']) != CUTOFF
            or set(contract['channels']) != {'wind','solar'}):
        raise ValueError('Missing held-out split or spatial channel contract')
    wind = manager._wind_artifact_paths('HR')
    solar = manager._solar_artifact_paths('HR')
    verified(wind['sites_meta'])
    if manager._resolve_wind_resolution('HR',wind_mode)[0] != 'nuts3':
        raise ValueError('The actual model selected a nonapproved wind resolution')
    paths = {'wind': (wind['nuts_profiles'],wind['transfer_nuts']),
             'solar': (solar['macro_profiles'],solar['transfer_macro'])}
    if inventory is not None:
        canonical = verified(Path(manager.DATA_DIR)/'genco_data_HR_sdewes.csv')
        if canonical != verified(root/genco_path):
            raise ValueError('The solver and weather mapper loaded different generator inventories')
    report = {}
    for channel,(profile,transfer) in paths.items():
        entry = contract['channels'][channel]
        profile,transfer = verified(profile),verified(transfer)
        if profile != (root/entry['profile']).resolve() or transfer != (root/entry['transfer']).resolve():
            raise ValueError('Scenario-manager path differs from approved spatial source: '+channel)
        record = json.loads(verified(root/entry['lineage']).read_bytes())
        if (record['channel'] != channel or record['profile_sha256'] != digest_file(profile)
                or record['transfer_sha256'] != digest_file(transfer)
                or record.get('includes_held_out_observations') is not False
                or not record.get('source_artifacts') or not record.get('fitting_stages')):
            raise ValueError('Incomplete physical or calibration lineage: '+channel)
        if channel == 'wind' and (record.get('meteorology_product') != 'ERA5'
                                  or record.get('turbine_curve') != 'Nordex'):
            raise ValueError('Wind profiles do not use the approved ERA5/Nordex conversion')
        for artifact in record['source_artifacts']:
            digest = artifact['sha256']
            if not artifact.get('role') or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid upstream source identity: '+channel)
        for step in record['fitting_stages']:
            if not step.get('name') or parse_utc(step['last_input_utc']) >= CUTOFF:
                raise ValueError('Held-out observations in spatial fitting: '+channel)
        frame = (read_parquet or pd.read_parquet)(profile)
        transfer_rows = pd.read_csv(transfer)
        if not set(transfer_rows['source_zone']).intersection(frame.columns):
            raise ValueError('Spatial profile would trigger an empty-mapping fallback: '+channel)
        if inventory is not None:
            expected = {unit: item['initial_mw'] for unit,item in inventory.items() if item['group'] == channel}
            mapped = getattr(manager,f'_get_{channel}_units')('HR')
            unit_rows = transfer_rows.groupby('unit_name')['unit_cap_mw']
            if (not expected or set(mapped) != set(expected) or set(unit_rows.groups) != set(expected)
                    or not set(transfer_rows['source_zone']) <= set(frame.columns)
                    or transfer_rows[['unit_name','source_zone']].duplicated().any()
                    or not transfer_rows['weight'].gt(0.).all()
                    or any(not isclose(mapped[u], mw, abs_tol=1e-7, rel_tol=0.)
                           or any(not isclose(v,mw,abs_tol=1e-7,rel_tol=0.) for v in unit_rows.get_group(u))
                           for u,mw in expected.items())):
                raise ValueError('Weather mapper, transfer and approved nameplate disagree: '+channel)
        time = frame['time'] if 'time' in frame else frame.index
        if not isinstance(time,pd.DatetimeIndex) and 'time' not in frame:
            raise ValueError('Missing original timestamp index: '+channel)
        index = pd.DatetimeIndex(pd.to_datetime(time,utc=True,errors='raise'))
        if (index.empty or index.hasnans or index.has_duplicates or not index.is_monotonic_increasing
                or index[-1] >= CUTOFF or not (index == index.floor('h')).all()):
            raise ValueError('Spatial profile includes held-out or malformed timestamps: '+channel)
        report[channel] = dict(first_utc=index[0].isoformat(),last_utc=index[-1].isoformat(),
                               hourly_rows=len(index),profile=str(profile.relative_to(root)),
                               transfer=str(transfer.relative_to(root)))
    return report

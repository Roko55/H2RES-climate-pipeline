"""Build the future spatial-input contract from the verified source cohort."""
import json
from math import isfinite
from pathlib import Path

from electricity_project_integration import digest_file


HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent
SC = Path('data/SC')
SOLAR = SC/'HR_solar_macro_profiles.parquet'
SOLAR_TRANSFER = SC/'HR_solar_transfer_macro_to_units.csv'
WIND = SC/'HR_wind_nuts_profiles.parquet'
WIND_TRANSFER = SC/'HR_wind_transfer_nuts_to_units.csv'
WIND_SITES = SC/'HR_wind_sites_meta.csv'
SOLAR_EVIDENCE_SHA = 'fa787c41b40d96c432390d1c7202c688c1c922015e5e8acf35a32e73c3bc1afd'
WIND_COHORT_SHA = '8e28dbf196123bea8432bc06b82f4fcfe1c5a2f9fc1ff67433b25a866c9b4213'
WIND_DECISION_SHA = '8e08e8ab61b0c4ae2d9b05f0c98c23246215af3ba7cd515d8bfe9aa0e5727bb8'


def frozen_json(path, expected):
    if digest_file(path) != expected:
        raise ValueError('Source evidence changed: '+str(path))
    return json.loads(path.read_bytes())


def read_solar_candidate(workspace=WORKSPACE):
    package = workspace/HERE.name/'training_solar_spatial_preflight'
    data = frozen_json(package/'preflight.json',SOLAR_EVIDENCE_SHA)
    profile = package/SOLAR.name
    transfer = workspace/'H2RES_SingleZone_Myopic_v1.2'/SOLAR_TRANSFER
    if (data['status'] != 'local_preflight_verified' or data['planning_approved']
            or data['last_input_utc'] != '2015-12-31T23:00:00+00:00'
            or data['hourly_rows'] != 315576 or data['threshold_mask_changes'] != 0
            or not data['legacy_cf_scalar_cancels_in_spatial_ratios']
            or not isfinite(data['max_ratio_change']) or not 0 <= data['max_ratio_change'] <= 1e-12
            or digest_file(profile) != data['profile_sha256']
            or digest_file(transfer) != data['transfer_sha256']):
        raise ValueError('Unscaled solar candidate is not equivalent or its source changed')
    if len(data['raw_inputs_sha256']) != 3:
        raise ValueError('Unscaled solar needs the three original zones')
    for name,expected in data['raw_inputs_sha256'].items():
        if digest_file(workspace/Path(name.replace('\\','/'))) != expected:
            raise ValueError('Raw zonal solar evidence changed: '+name)
    lineage = dict(channel='solar',profile_sha256=data['profile_sha256'],transfer_sha256=data['transfer_sha256'],
                   includes_held_out_observations=False,
                   source_artifacts=[dict(role=name,sha256=value) for name,value in data['raw_inputs_sha256'].items()],
                   fitting_stages=[dict(name='unscaled spatial pooling',last_input_utc=data['last_input_utc'])],
                   equivalent_to_archived_spatial_ratios_max_abs=data['max_ratio_change'],
                   archived_threshold_mask_changes=0,legacy_common_calibration_scalar_applied=False)
    return profile,transfer,lineage


def read_wind_candidate(verified_wind_root, workspace=WORKSPACE):
    provenance = workspace/'audit_pipeline_20261030/sources'
    cohort = frozen_json(provenance/'8e28dbf19612_climate_sources.json',WIND_COHORT_SHA)
    decision = frozen_json(provenance/'8e08e8ab61b0_conversion_decision.json',WIND_DECISION_SHA)
    files = {relative:verified_wind_root/relative for relative in (WIND,WIND_TRANSFER,WIND_SITES)}
    for relative,path in files.items():
        if digest_file(path) != cohort['artifacts'][relative.name]['solver_file_sha256']:
            raise ValueError('Not the verified stationary wind file: '+str(relative))
    artifact = cohort['artifacts'][WIND.name]
    if (artifact['last_year'] != 2015 or decision['reporting_used_for_this_coefficient'] is not False
            or not decision['selection'].startswith('2015 January-July training fit,')
            or decision['correction_domain'] != 'wind_speed_before_curve'
            or not decision['curve_mode'].startswith('Nordex N131/3600')
            or decision['speed_correction'] != 1.3247997658871304):
        raise ValueError('Unverified wind conversion or training boundary')
    lineage = dict(channel='wind',profile_sha256=artifact['solver_file_sha256'],
                   transfer_sha256=cohort['artifacts'][WIND_TRANSFER.name]['solver_file_sha256'],
                   includes_held_out_observations=False,meteorology_product='ERA5',turbine_curve='Nordex',
                   source_artifacts=[dict(role='full wind NUTS cohort',sha256=artifact['full_source_sha256']),
                                     dict(role='conversion decision',sha256=WIND_DECISION_SHA)],
                   fitting_stages=[dict(name='2015 HOPS speed fit',last_input_utc='2015-07-31T23:00:00Z'),
                                   dict(name='training spatial pooling',last_input_utc='2015-12-31T23:00:00Z')],
                   speed_correction=decision['speed_correction'])
    return files,lineage


def build_spatial_payload(verified_wind_root, workspace=WORKSPACE):
    """Return source paths and manifest bytes; deployment belongs to the future fenced runner."""
    solar,transfer,solar_lineage = read_solar_candidate(workspace)
    files,wind_lineage = read_wind_candidate(verified_wind_root,workspace)
    files.update({SOLAR:solar,SOLAR_TRANSFER:transfer})
    return files, spatial_contract_documents(solar_lineage,wind_lineage)


def spatial_contract_documents(solar_lineage, wind_lineage):
    names = {'solar':SC/'solar_training_lineage.json','wind':SC/'wind_training_lineage.json'}
    contract = dict(held_out_from_utc='2020-01-01T00:00:00Z',channels={
        'solar':dict(profile=SOLAR.as_posix(),transfer=SOLAR_TRANSFER.as_posix(),lineage=names['solar'].as_posix()),
        'wind':dict(profile=WIND.as_posix(),transfer=WIND_TRANSFER.as_posix(),lineage=names['wind'].as_posix()),
    })
    manifests = {Path('spatial_sources.json'):contract,names['solar']:solar_lineage,names['wind']:wind_lineage}
    return {path:json.dumps(data,sort_keys=True,indent=2).encode() for path,data in manifests.items()}


def write_stock_matched_contract(project, solar_training, solar_stock, archived, wind_decision_path):
    """Record the new transfers while preserving the training-only source evidence."""
    decision = frozen_json(wind_decision_path,WIND_DECISION_SHA)
    wind = archived['artifacts'][WIND.name]
    solar_sha = digest_file(project/SOLAR)
    wind_sha = digest_file(project/WIND)
    if (solar_sha != solar_training['profile_sha256']
            or solar_training['last_input_utc'] != '2015-12-31T23:00:00+00:00'
            or len(solar_training['raw_inputs_sha256']) != 3
            or digest_file(project/SOLAR_TRANSFER) != solar_stock['candidate_transfer_sha256']
            or wind_sha != wind['solver_file_sha256'] or wind['last_year'] != 2015
            or decision['reporting_used_for_this_coefficient'] is not False
            or not decision['selection'].startswith('2015 January-July training fit,')
            or decision['correction_domain'] != 'wind_speed_before_curve'
            or not decision['curve_mode'].startswith('Nordex N131/3600')
            or decision['speed_correction'] != 1.3247997658871304):
        raise ValueError('Unexpected generation profile calibration or dated stock transfer')
    solar_lineage = dict(channel='solar',profile_sha256=solar_sha,
                         transfer_sha256=solar_stock['candidate_transfer_sha256'],
                         includes_held_out_observations=False,
                         source_artifacts=[dict(role=name,sha256=digest)
                                           for name,digest in solar_training['raw_inputs_sha256'].items()],
                         fitting_stages=[dict(name='unscaled spatial pooling',last_input_utc=solar_training['last_input_utc'])])
    wind_lineage = dict(channel='wind',profile_sha256=wind_sha,
                        transfer_sha256=digest_file(project/WIND_TRANSFER),includes_held_out_observations=False,
                        meteorology_product='ERA5',turbine_curve='Nordex',
                        source_artifacts=[dict(role='full wind NUTS cohort',sha256=wind['full_source_sha256']),
                                          dict(role='conversion decision',sha256=WIND_DECISION_SHA)],
                        fitting_stages=[dict(name='2015 HOPS speed fit',last_input_utc='2015-07-31T23:00:00Z'),
                                        dict(name='training spatial pooling',last_input_utc='2015-12-31T23:00:00Z')],
                        speed_correction=decision['speed_correction'])
    for relative,data in spatial_contract_documents(solar_lineage,wind_lineage).items():
        with (project/relative).open('xb') as stream:
            stream.write(data)


def render_capacity_aware_cache_keys(source):
    """Keep rebuilt weights isolated when one preparation process sees two fleets."""
    for prefix, units in (('"solar_spatial", cc', 'solar_units'),
                          ('"wind_spatial", cc, res', 'wind_units')):
        old = f'cache_key = ({prefix})'
        if source.count(old) != 1:
            raise ValueError(f'Unexpected spatial cache key for {units}')
        key = f'tuple(sorted((name, float(mw)) for name, mw in {units}.items()))'
        source = source.replace(old, f'cache_key = ({prefix}, {key})')
    compile(source, '<capacity-aware spatial preparation>', 'exec')
    return source

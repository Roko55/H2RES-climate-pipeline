"""Stage the isolated seven-path national input set; this module never solves."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket

from electricity_project_integration import digest_file
from multisector_exchange_integration import render_country_build
from multisector_hydro_integration import render_unique_hydro_capex
from multisector_planning_integration import CASES, render_dry_build_worker, render_reference_worker, render_worker
from national_average_diagnostic import DONOR, REMOTE, render_stationary_manager
from prepare_spatial_training_contract import write_stock_matched_contract
from qualify_future_demand_inputs import write_demand_receipt
from qualify_national_stock import GENCO, SEED, SUPPLY, qualify_national_stock, read, write_new
from seed_planning_investments import HORIZON


MODEL = Path('scripts/Build_model_RH.py')
READER = Path('scripts/read_process_data.py')
MANAGER = Path('scripts/scenario_manager.py')
SOLAR = Path('data/SC/HR_solar_transfer_macro_to_units.csv')
WIND = Path('data/SC/HR_wind_transfer_nuts_to_units.csv')
LEGACY = Path('baseline/legacy_whole_path_03/mapping_20261030')


def copy_checked(source, dest, digest):
    if digest_file(source) != digest or dest.exists():
        raise ValueError('Unqualified copy or existing destination: '+str(dest))
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,dest)
    if digest_file(dest) != digest:
        raise ValueError('Copied content changed: '+str(dest))


def write_code(path, source):
    compile(source,str(path),'exec')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:
        stream.write(source)


def project_hashes(project):
    return {path.relative_to(project).as_posix():digest_file(path)
            for path in sorted(project.rglob('*')) if path.is_file()}


def stage(root, diagnostic):
    if (socket.gethostname().lower() != 'pcsp-mxl0493vpr' or root.parent != REMOTE
            or not root.name.startswith('national_production_') or diagnostic.parent != REMOTE
            or not diagnostic.name.startswith('national_average_matrix_')):
        raise ValueError('Stage only in a fresh owned directory on the H2RES server')
    with (root/'stage.lock').open('x') as stream:
        stream.write(str(os.getpid()))
    if (root/'preparation.json').exists():
        raise ValueError('The staged production set is immutable')
    inputs = read(diagnostic/'matrix_inputs.json')
    donor = read(DONOR/'preparation.json')
    upstream = donor['frozen_files']
    model_input_paths = {Path(name) for name in inputs['uploaded_inputs'] if name.startswith('data/')}
    replaced = model_input_paths | {GENCO, READER, WIND, SOLAR}
    for name in ('historical_terminal.json','historical_physical_audit.json'):
        copy_checked(diagnostic/'inputs'/name,
                     root/SEED/('independent_physical_audit.json' if name.startswith('historical_physical') else name),
                     inputs['uploaded_inputs'][name])
    for name in ('audited_export_helpers.py','hooks.py','sources/post_audit.py',
                 'baseline/helpers/precision_policy.py',
                 (LEGACY/'results/repaired_avg/legacy_runner.py').as_posix(),
                 (LEGACY/'projects/repaired_avg/review_command.json').as_posix()):
        copy_checked(DONOR/name,root/name,upstream[name])
    plan_source = DONOR/'plan.json'
    if digest_file(plan_source) != upstream['plan.json']:
        raise ValueError('Stationary donor plan has changed')
    plan = read(plan_source)
    if plan['required_new_planning_windows'] != len(CASES)*len(HORIZON):
        raise ValueError('Unexpected donor planning scope')
    plan.update(required_new_planning_windows=len(CASES)*(len(HORIZON)-1),
                source_plan_sha256=upstream['plan.json'],
                production_anchors=list(HORIZON[1:]),physical_seed_year=HORIZON[0],
                completion='42 new native planning windows from a common physical 2020 seed.')
    write_new(root/'plan.json',plan)
    common = (DONOR/'common.py').read_text(encoding='utf-8-sig')
    if digest_file(DONOR/'common.py') != upstream['common.py'] or common.count(DONOR.as_posix()) != 1:
        raise ValueError('Unexpected stationary controller root guard')
    write_code(root/'common.py',common.replace(DONOR.as_posix(),root.as_posix()))
    raw = (root/'worker_source.py').read_text(encoding='utf-8-sig')
    for name, render in (('full_worker.py',render_worker),('reference_worker.py',render_reference_worker),
                         ('dry_build_worker.py',render_dry_build_worker)):
        write_code(root/name,render(raw,root.as_posix()))
    stock_base = None
    solar_evidence = read(diagnostic/'inputs/solar_training_evidence.json')
    solar_stock = read(diagnostic/'inputs/solar.json')
    for case in CASES:
        archived = DONOR/'projects'/case
        project = root/'projects'/case
        project.mkdir(parents=True,exist_ok=False)
        for source in sorted(archived.rglob('*')):
            relative = source.relative_to(archived)
            key = f'projects/{case}/{relative.as_posix()}'
            if (not source.is_file() or '__pycache__' in relative.parts or relative.parts[0]=='results'
                    or relative in replaced or key not in upstream):
                continue
            if relative in (MODEL,MANAGER):
                if digest_file(source) != upstream[key]:
                    raise ValueError('Changed model source: '+key)
                text = source.read_text(encoding='utf-8-sig')
                text = (render_unique_hydro_capex(render_country_build(text)) if relative==MODEL
                        else render_stationary_manager(text))
                write_code(project/relative,text)
            else:
                copy_checked(source,project/relative,upstream[key])
        for relative in replaced:
            copy_checked(diagnostic/'project'/relative,project/relative,inputs['staged_sha256'][relative.as_posix()])
        if digest_file(project/'data/SC/xgb_synthetic_scenarios.npy') != donor['ensemble_sha256']:
            raise ValueError('Unexpected synthetic weather input for '+case)
        write_stock_matched_contract(project,solar_evidence,solar_stock,read(archived/'climate_sources.json'),
                                     root/'wind_conversion_decision.json')
        write_demand_receipt(project,case,project_hashes(project))
        if case == 'average':
            stock_base = qualify_national_stock(root,project,diagnostic)
            copy_checked(project/MODEL,root/'sources'/MODEL.name,digest_file(project/MODEL))
        else:
            copy_checked(root/'projects/average'/SUPPLY,project/SUPPLY,stock_base['production_supply_inventory_sha256'])
    volatile = {'stage.lock','stage.log','launch.lock','launch.json'}
    frozen = {name:digest for name,digest in project_hashes(root).items()
              if name not in volatile and not name.startswith(('records/','jobs/','__pycache__/'))}
    write_new(root/'preparation.json',dict(status='staged_for_common_input_capture',
               stationarity_source_sha256=donor['ensemble_sha256'],stationary_channel_design=True,
               historical_base_sha256=frozen['historical_base.json'],
               execution_fence=sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest(),frozen_files=frozen))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('diagnostic',type=Path)
    stage(Path(__file__).resolve().parent,p.parse_args().diagnostic.resolve())

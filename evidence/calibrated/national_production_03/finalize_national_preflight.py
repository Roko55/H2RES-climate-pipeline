"""Pin the six no-solve references, then admit a verified national dry build."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import socket

from electricity_project_integration import digest_file
from multisector_planning_integration import verify_window_reference
from qualify_national_stock import read, write_new
from seed_planning_investments import HORIZON


AUDITS = (
    'production_supply_audit.json', 'country_exchange_build_audit.json', 'fixed_demand_build_audit.json',
    'stationary_storage_build_audit.json', 'hydro_build_audit.json', 'hydrogen_build_audit.json',
    'district_heat_build_audit.json', 'power_policy_build_audit.json', 'dispatchable_renewables_build_audit.json',
)


def pin_references(root):
    path = root/'preparation.json'
    preparation = read(path)
    if preparation['status'] == 'references_pinned':
        for i in range(1,len(HORIZON)):
            verify_window_reference(root,preparation['frozen_files'],HORIZON[i:i+2])
        return preparation
    if preparation['status'] != 'staged_for_common_input_capture':
        raise ValueError('Unexpected reference staging state')
    frozen = preparation['frozen_files']
    for name,digest in frozen.items():
        if digest_file(root/name) != digest:
            raise ValueError('Staged production input changed before reference capture: '+name)
    for i, year in enumerate(HORIZON[1:],1):
        capture = root/'input_reference_capture'/str(year)
        record = read(capture/'capture.json')
        receipt = capture/str(year)/'prebuild_validation.json'
        digest = digest_file(receipt)
        if (record['status'] != 'common_input_reference_captured_no_build' or record['year'] != year
                or record['solver_invocations'] != 0 or record['receipt_sha256'] != digest
                or record['execution_fence'] != preparation['execution_fence']
                or record['historical_base_sha256'] != preparation['historical_base_sha256']):
            raise ValueError('Reference does not come from the immutable national configuration')
        key = f'reference/{year}/prebuild_validation.json'
        dest = root/key
        dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():
            with dest.open('xb') as stream:
                stream.write(receipt.read_bytes())
        frozen[key] = digest
        verify_window_reference(root,frozen,HORIZON[i:i+2])
    preparation['execution_fence'] = sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
    preparation['status'] = 'references_pinned'
    pending = path.with_suffix('.pinned.pending')
    write_new(pending,preparation)
    pending.replace(path)
    return preparation


def approve_dry_build(root):
    preparation = read(root/'preparation.json')
    if preparation['status'] != 'references_pinned':
        raise ValueError('No common references are pinned')
    for i in range(1,len(HORIZON)):
        verify_window_reference(root,preparation['frozen_files'],HORIZON[i:i+2])
    folder = root/'dry_build_jobs/average'
    report = read(folder/'dry_build.json')
    if (report['status'] != 'matrix_built_without_optimization' or report['solver_invocations'] != 0
            or report['fence'] != preparation['execution_fence'] or report['case'] != 'average'
            or report['years'] != [2025,2030] or not all(report[k]>0 for k in ('rows','columns'))
            or report['historical_base_sha256'] != preparation['historical_base_sha256']
            or any(folder.rglob('accepted.json'))):
        raise ValueError('The accepted production input set has no qualified dry build')
    reports = [folder/'dry_build.json',*(folder/'2025'/name for name in AUDITS)]
    evidence = {file.relative_to(root).as_posix():digest_file(file) for file in reports}
    write_new(root/'production_ready.json',dict(status='ready_for_solver_admission',
              preparation_sha256=digest_file(root/'preparation.json'),execution_fence=report['fence'],
              dry_build_fingerprint=report['fingerprint'],evidence_sha256=evidence))


def require_national_admission(root):
    ready = read(root/'production_ready.json')
    preparation = read(root/'preparation.json')
    if (ready['status'] != 'ready_for_solver_admission'
            or ready['preparation_sha256'] != digest_file(root/'preparation.json')
            or ready['execution_fence'] != preparation['execution_fence']
            or len(ready['evidence_sha256']) != len(AUDITS)+1):
        raise ValueError('Production admission gate is incomplete')
    for name,expected in ready['evidence_sha256'].items():
        file = (root/name).resolve()
        if not file.is_relative_to(root.resolve()) or digest_file(file) != expected:
            raise ValueError('Production gate evidence changed: '+name)
    return preparation


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=('references','dry-build'))
    args = p.parse_args()
    root = Path(__file__).resolve().parent
    if (socket.gethostname().lower()!='pcsp-mxl0493vpr' or root.parent != Path('E:/Roko/remote_access')
            or not root.name.startswith('national_production_')):
        raise ValueError('Use an owned production directory on the H2RES server')
    (pin_references if args.action=='references' else approve_dry_build)(root)

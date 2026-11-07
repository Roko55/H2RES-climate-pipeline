"""Qualify the common planning inventory from the independent native matrix."""

import csv
import json
from math import fsum, isclose
from pathlib import Path

from audit_production_families import supply_inventory
from electricity_project_integration import digest_file
from seed_planning_investments import CAPITAL_FAMILIES


GENCO = Path('data/SC/genco_data_HR_sdewes.csv')
STOCK = 'production_stock_reconciliation.json'
SUPPLY = 'production_supply_inventory.json'
SEED = Path('national_seed_evidence')
CHECKS = {'supply','exchange','demand','storage','hydro','hydrogen','district_heat',
          'power_policy','dispatchable_renewables'}


def read(path):
    return json.loads(path.read_bytes())


def write_new(path,data):
    with path.open('x',encoding='utf8') as stream:
        json.dump(data,stream,indent=2,sort_keys=True,allow_nan=False)
        stream.write('\n')


def qualify_national_stock(root, project, diagnostic):
    """Authorize input staging only after the actual native capacity evidence passes."""
    manifest_path = diagnostic/'matrix_inputs.json'
    matrix_path = diagnostic/'matrix_report.json'
    report, inputs = read(matrix_path), read(manifest_path)
    gate = report.get('checks',{})
    state = report.get('zero_prior_investment_keys',{})
    families = {f'{name}_cumulative' for name in CAPITAL_FAMILIES} | {f'values_{name}' for name in CAPITAL_FAMILIES}
    if (report['status'] != 'diagnostic_matrix_built' or report['optimizer_calls'] != 0
            or report['checkpoint_written'] is not False or report['years'] != [2025,2030]
            or report['input_manifest_sha256'] != digest_file(manifest_path)
            or set(gate) != CHECKS or any(gate[name]['passed'] is not True for name in CHECKS)
            or set(state) != families
            or any(state[k] != 0 for k in state if k.startswith('values_'))
            or any(state[k] <= 0 for k in state if k.endswith('_cumulative'))):
        raise ValueError('National future matrix or its empty native capital state did not qualify')
    inventory_path = project/GENCO
    stock_hash = digest_file(inventory_path)
    if stock_hash != inputs['staged_sha256'][GENCO.as_posix()]:
        raise ValueError('Production generator inventory differs from the qualified model')
    with inventory_path.open(encoding='utf-8-sig',newline='') as stream:
        rows = list(csv.DictReader(stream))
    inventory = supply_inventory(rows)
    groups = {item['group'] for item in inventory.values()}
    mw_by_group = {group:fsum(x['initial_mw'] for x in inventory.values() if x['group']==group) for group in groups}
    supply = gate['supply']['detail']
    if (supply['hours'] != 8760 or supply['units'] != len(inventory)
            or set(supply['initial_capacity_by_group_mw']) != groups
            or any(not isclose(supply['initial_capacity_by_group_mw'][g],mw,abs_tol=1e-7,rel_tol=0.)
                   for g,mw in mw_by_group.items())):
        raise ValueError('Approved inventory groups differ from the native supply audit')
    renewable_caps = {row['unit_name']:float(row['max_inv_period']) for row in rows
                      if row['technology'] in ('WTON','PHOT')}
    if (len(renewable_caps) != 8 or any(v<=0. for v in renewable_caps.values())
            or any(float(row['cap_inv_cost']) < 10_000. for row in rows if float(row['max_inv_period']) > 0.)
            or any(float(row['max_inv_period']) != 0. for row in rows if row['fuel_type']=='Nuclear')):
        raise ValueError('Missing renewable investment, placeholder CAPEX or unexpected nuclear investment')
    terminal_path, physical_path = root/SEED/'historical_terminal.json', root/SEED/'independent_physical_audit.json'
    historical = read(terminal_path)
    if (read(physical_path)['status'] != 'passed' or historical['status'] != 'solved'
            or historical['solver_status'] != 2 or not 0. <= historical['maximum_new_capacity'] <= 1e-7):
        raise ValueError('Historical no-build capital or physical evidence is missing')
    write_new(project/SUPPLY,inventory)
    supply_hash = digest_file(project/SUPPLY)
    reconciliation = dict(status='passed',scope='multisector',all_native_families_reconciled=True,
                          evidence_scope='existing stock and first native future build; zero historical additions',
                          production_inventory_sha256=stock_hash,production_supply_inventory_sha256=supply_hash,
                          matrix_sha256=digest_file(matrix_path),matrix_input_sha256=digest_file(manifest_path),
                          matrix_fingerprint=report['fingerprint'],native_zero_investment_keys=state,
                          direct_supply_by_group_mw=mw_by_group,
                          stationary_storage=gate['storage']['detail']['existing'],
                          district_heat=gate['district_heat']['detail'],hydrogen=gate['hydrogen']['detail'])
    write_new(root/STOCK,reconciliation)
    paths = {'historical_terminal':terminal_path,'independent_physical_audit':physical_path,
             'production_stock_reconciliation':root/STOCK}
    base = dict(year=2020,scope='multisector',planning_approved=True,status='accepted',
                solver_status=historical['solver_status'],maximum_new_capacity=historical['maximum_new_capacity'],
                production_inventory_sha256=stock_hash,production_supply_inventory_sha256=supply_hash,
                evidence_sha256={key:dict(path=path.relative_to(root).as_posix(),sha256=digest_file(path))
                                 for key,path in paths.items()})
    write_new(root/'historical_base.json',base)
    return base

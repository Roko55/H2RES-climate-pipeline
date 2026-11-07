"""Read and reconcile the completed 2030 zonal production receipts."""

import argparse
import csv
import gzip
import json
from itertools import product
from math import fsum, isclose, isfinite
from pathlib import Path

from electricity_project_integration import digest_file


CASES = ('avg','low','high')
YEARS = range(2020,2025)
FRACTIONS = (1.,.5,0.)
TOL = 1e-7
ZONES = {'HR','SI','BA'}


def read(path):
    return json.loads(path.read_bytes())


def verify_file(root, relative, sha):
    path = (root/relative).resolve()
    if not path.is_relative_to(root.resolve()) or digest_file(path)!=sha:
        raise ValueError('Changed or external result file: '+str(path))
    return path


def close(actual,expected=0.,tol=TOL):
    return isfinite(actual) and isclose(actual,expected,rel_tol=0.,abs_tol=tol)


def inspect_hourly(path, data, physical, name):
    observed = {}
    grid_in = ('generation_mwh','eens_mwh','import_delivered_mwh','battery_discharge_grid_mwh')
    grid_out = ('load_mwh','ceep_mwh','gross_export_sending_mwh','battery_charge_grid_mwh','pumped_charge_grid_mwh')
    water_in = ('natural_reservoir_mwh_e','natural_ror_mwh_e','natural_pumped_mwh_e','hydro_pump_credit_mwh_e')
    water_out = ('reservoir_generation_mwh','ror_generation_mwh','pumped_generation_mwh',
                 'hydro_spill_mwh_e','ror_unused_mwh_e','hydro_storage_delta_mwh_e')
    keys = set(grid_in+grid_out+water_in+water_out) | {'gross_import_pre_loss_mwh',
                                                       'battery_overlap_grid_mwh','pumped_overlap_grid_mwh'}
    with gzip.open(path,'rt',encoding='utf8',newline='') as stream:
        rows = csv.DictReader(stream)
        if not keys | {'system','period'} <= set(rows.fieldnames or []):
            raise ValueError('Missing native hour fields: '+name)
        for zone in ('HR','SI','BA'):
            values = {k:[] for k in keys}
            for period in range(1,8761):
                row = next(rows,None)
                if row is None or (row['system'],int(row['period']))!=(zone,period):
                    raise ValueError('Missing or duplicated electrical hour: '+name)
                h = {k:float(row[k]) for k in keys}
                if any(not isfinite(v) or (k!='hydro_storage_delta_mwh_e' and v < -TOL) for k,v in h.items()):
                    raise ValueError('Invalid actual hourly data: '+name)
                for incoming,outgoing in ((grid_in,grid_out),(water_in,water_out)):
                    if not close(fsum(h[k] for k in incoming)-fsum(h[k] for k in outgoing)):
                        raise ValueError(f'Native energy does not close: {name}/{zone}/{period}')
                for k,v in h.items():
                    values[k].append(v)
            sums = {k:fsum(v) for k,v in values.items()}
            observed[zone] = sums
            for key in ('eens','ceep'):
                k = key+'_mwh'
                if (not close(fsum(max(0.,v) for v in values[k]),data['service'][zone][k],8760*TOL)
                        or sum(v>TOL for v in values[k])!=data['service'][zone][key+'_hours']):
                    raise ValueError('Hourly service differs from the accepted aggregate: '+name)
            if (not close(sums['load_mwh'],physical['demand_annual_mwh'][zone])
                    or not close(sums['hydro_storage_delta_mwh_e'],tol=2*TOL)):
                raise ValueError('Hourly load or annual water state differs: '+name)
            for k,v in sums.items():
                if not close(v,data['hourly']['zones'][zone]['annual'][k]):
                    raise ValueError('Independent hourly aggregate differs: '+name+'/'+zone+'/'+k)
        if next(rows,None) is not None:
            raise ValueError('Unexpected extra electrical hours: '+name)
    hr = observed['HR']
    return {k:hr[k] for k in ('battery_overlap_grid_mwh','pumped_overlap_grid_mwh',
                              'hydro_spill_mwh_e','ror_unused_mwh_e')}


def inspect_job(root, index, fence, case, weather, fraction, parent):
    name = f'planning_{case}' if weather is None else f'operation_{case}_{weather}_{int(fraction*100)}'
    markers = list((root/'jobs'/name).glob('attempt_*/accepted.json'))
    if len(markers)!=1 or name not in index:
        raise ValueError('Missing or duplicated accepted job: '+name)
    marker, = markers
    verify_file(root,marker.relative_to(root),index[name]['marker_sha256'])
    data = read(marker)
    if (data['diagnostic_sha256']!=fence or data['hours']!=8760
            or (data['case'],data['weather'],data['fraction'])!=(case,weather,fraction)
            or data['status']!=index[name]['status'] or data['no_new_investment_choices']!=(weather is not None)):
        raise ValueError('Accepted result identity mismatch: '+name)
    artifacts = {n:verify_file(marker.parent,n,d) for n,d in data['files'].items()}
    receipt = read(artifacts['matrix.json'])
    matrix, physical = receipt['native'],receipt['physical']
    if (matrix['year']!=2030 or matrix['hours']!=8760 or matrix['zone_balances']!=26280
            or matrix['exchange']['fraction']!=fraction or matrix['krsko_mw']!=696
            or physical['generation']['checked_dated_units']!=61 or set(physical['demand_annual_mwh'])!=ZONES
            or physical['si_ba_directional_rows']!=35040 or physical['battery']['power_rows']!=26280
            or len(physical['hydro'])!=8 or len(receipt['capex'])!=11):
        raise ValueError('Incorrect actual model boundary: '+name)
    for zone,expected in {'HR':20012798.482489,'SI':16462900.001556,'BA':11799999.99175}.items():
        if not close(physical['demand_annual_mwh'][zone],expected,1e-4):
            raise ValueError('Wrong exogenous 2030 demand: '+zone)

    solver = data['solver']
    from_log = read(artifacts['solver_status.json'])
    if (from_log['solver_status'],from_log['fingerprint'])!=(solver['solver_status'],solver['fingerprint']):
        raise ValueError('Solver report differs from accepted record: '+name)
    if weather is not None and data['parent_portfolio_sha256']!=parent['files']['portfolio.json']:
        raise ValueError('Wrong portfolio handoff: '+name)
    if data['status']=='verified_infeasible_operation':
        if (weather is None or solver['solver_status']!=3
                or data['files']['infeasible.ilp']!=solver['iis_sha256']):
            raise ValueError('Infeasibility lacks solver and IIS evidence: '+name)
        return data, dict(job=name,status=data['status'])
    if (data['status']!=('optimal_planning' if weather is None else 'optimal_operation')
            or solver['solver_status']!=2
            or any(not 0<=solver[n]<=TOL for n in ('bound_violation','constraint_violation'))
            or data['exchange']['rows']!=87600 or data['exchange']['solved'] is not True
            or not close(data['exchange']['worst_solved_excess_mw'])
            or data['neighbor_exchange']['rows']!=35040
            or not close(data['neighbor_exchange']['worst_excess_mw'])):
        raise ValueError('Unverified optimal dispatch or exchange: '+name)
    if weather is not None and (not close(data['max_fixed_stock_delta'])
            or not close(data['max_storage_cycle_error']) or data['storage_cycles']<=0):
        raise ValueError('Nonfixed investment stock or noncyclic storage: '+name)
    stock = read(artifacts['portfolio.json'])
    if not stock:
        raise ValueError('Missing solved stock: '+name)
    if weather is not None:
        original, = (root/'jobs'/f'planning_{case}').glob('attempt_*/accepted.json')
        reference = read(verify_file(original.parent,'portfolio.json',parent['files']['portfolio.json']))
        if (stock.keys()!=reference.keys() or any(vals.keys()!=reference[n].keys()
                or any(not close(v,reference[n][k]) for k,v in vals.items()) for n,vals in stock.items())):
            raise ValueError('Solved stock differs from its planning portfolio: '+name)
    objective = data['objective']
    if (not close(fsum((objective['capex'],objective['fixed_opex'],objective['variable_opex'],
                       -solver['objective_model_units'])))
            or not close(objective['operating_excluding_capex'],fsum((objective['fixed_opex'],objective['variable_opex'])))):
        raise ValueError('Investment and operating costs do not close: '+name)
    service = data['service']
    if (set(service)!=ZONES or any(not isfinite(v) or v<0. for row in service.values() for v in row.values())
            or any(not 0<=row[k]<=8760 or not isinstance(row[k],int)
                   for row in service.values() for k in ('eens_hours','ceep_hours'))):
        raise ValueError('Invalid energy-service metrics: '+name)
    croatia = data['croatia']
    for left,right in (('eens','unserved'),('ceep','curtailed')):
        if not close(service['HR'][left+'_mwh'],croatia[right+'_mwh']) or service['HR'][left+'_hours']!=croatia[right+'_hours']:
            raise ValueError('Croatian service meters disagree: '+name)
    if fraction==0. and any(not close(croatia[k],tol=8760*TOL)
                            for k in ('gross_import_mwh','gross_export_mwh')):
        raise ValueError('Measured nonzero Croatian gross exchange in the isolated case: '+name)
    hourly = inspect_hourly(artifacts['hourly.csv.gz'],data,physical,name)
    return data, dict(job=name,status=data['status'],**service['HR'],**{k:croatia[k] for k in
                      ('gross_import_mwh','gross_export_mwh','stationary_simultaneous_hours')},**hourly)


def audit(root):
    state = read(root/'status.json')
    if state['stage']!='completed_verified' or state['total']!=48:
        raise ValueError('Zonal experiment is still incomplete')
    results = read(verify_file(root,'results.json',state['results_sha256']))
    records = results['cases']
    index = {r['job']:r for r in records}
    if len(records)!=48 or len(index)!=48 or state['completed']!=records:
        raise ValueError('Final result index disagrees with the controller')
    fence = results['diagnostic_sha256']
    matching = [p for p in root.parent.glob('zonal_2030_matrix_*/matrix_report.json') if digest_file(p)==fence]
    if len(matching)!=1:
        raise ValueError('Missing or ambiguous originating electricity matrix diagnostic')
    verified_matrix = read(verify_file(matching[0].parent,'matrix_report.json',fence))
    if verified_matrix['no_optimization'] is not True:
        raise ValueError('Missing independent native matrix diagnostic')
    parent = {}
    output = []
    for case in CASES:
        parent[case], report = inspect_job(root,index,fence,case,None,1.,None)
        output.append(report)
    if len({r['weather_overlay_sha256'] for r in parent.values()})!=3:
        raise ValueError('The three planning cases have identical weather provenance')
    overlays = {}
    for year,fraction,case in product(YEARS,FRACTIONS,CASES):
        data,report = inspect_job(root,index,fence,case,year,fraction,parent[case])
        overlay = data['weather_overlay_sha256']
        if overlays.setdefault(year,overlay)!=overlay:
            raise ValueError('One historical weather year changed across portfolios or exchange limits')
        output.append(report)
    if len(set(overlays.values()))!=5 or set(overlays.values()) & {r['weather_overlay_sha256'] for r in parent.values()}:
        raise ValueError('Distinct historical and synthetic weather inputs are required')
    return dict(status='verified',portfolios=3,operation_cases=45,evidence=output)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root',type=Path)
    print(json.dumps(audit(p.parse_args().root.resolve()),indent=2,allow_nan=False))

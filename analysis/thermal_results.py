"""Reproduce V6.2 tables from the hash-bound, accepted thermal result extract.

This is a compact extract, not a replacement for the native hourly result stores.
The included source auditor checked all 42 original windows and state handoffs.
"""
from pathlib import Path
from hashlib import sha256
import json,math
import numpy as np
import pandas as pd

ACTIVE=('electricity_generation_cost','fuel_cell_generation_cost','optimized_import_cost',
    'ramping_cost','generation_investment_cost','hydrogen_and_fuel_cell_cost',
    'heat_sector_investment_cost','heat_sector_operation_cost','industry_fuel_cost',
    'co2_cost','storage_and_flexibility_cost','spill_and_ceep_cost','no_load_cost','extra_investment_cost')

def load(root):
    return json.loads((root/'evidence/thermal/national_plot_inputs.json').read_bytes())['national']

def annual_costs(root):
    out=[]
    for r in load(root):
        demand=math.fsum(r['demands'].values())
        out.append(dict(case=r['case'],year=r['year'],year_total_EUR=r['year_total'],
            capacity_terms_EUR=r['inv'],other_active_terms_EUR=r['year_total']-r['inv'],
            demand_MWh=demand,EUR_per_demand_MWh=r['year_total']/demand))
    return pd.DataFrame(out)

def national_tables(root):
    from scripts.channel_profiles import DONORS
    from scripts.release_runtime import accepted_array
    ensemble=accepted_array(root)
    proof=json.loads((root/'evidence/thermal/preserved_national_audit.json').read_bytes())
    audited={(r['case'],r['year']):r for c in proof['evidence'] for r in c['evidence']}
    objectives=[];components=[];investments=[];outcomes=[];inputs=[];thermal=[]
    for r in load(root):
        c,y=r['case'],r['year'];a=audited[c,y];donors=DONORS[c]
        profile=np.column_stack([ensemble[m,:,i] for i,m in enumerate(donors)])
        objectives.append(dict(case=c,year=y,window_years=json.dumps([y,y+5] if y<2050 else [y]),
            objective=r['obj'],constraint_violation=a['constraint_violation'],bound_violation=a['bound_violation']))
        for name,value in r['components'].items():
            components.append(dict(case=c,window_start=y,component_group=name,
                active_in_objective=int(name in ACTIVE),value=value))
        for key,value in r['cap'].items():
            family,unit=key.split('|',1)
            investments.append(dict(case=c,window_start=y,decision_year=y,realized=1,
                family=family,key_json=json.dumps([unit,y]),capacity_addition=1,
                unit='MWh' if family=='stat_sto_cap_inv' else 'MW',value=value))
        for key,value in r['supply'].items():
            family,indices=key.split('|',1)
            outcomes.append(dict(case=c,year=y,family=family,key_json=indices,value=value))
        inputs.append(dict(case=c,year=y,solar_member=donors[0],wind_member=donors[1],
            temperature_member=donors[2],profile_sha256=sha256(profile.tobytes()).hexdigest(),
            mean_solar_CF=profile[:,0].astype(float).mean(),mean_wind_CF=profile[:,1].astype(float).mean(),
            mean_temperature_C=profile[:,2].astype(float).mean(),
            heating_degree_hours_18C=np.maximum(18-profile[:,2].astype(float),0).sum(),
            cooling_degree_hours_22C=np.maximum(profile[:,2].astype(float)-22,0).sum()))
        thermal.extend(dict(case=c,window_start=y,year=y,**s) for s in r['input_statistics'])
    frame=pd.DataFrame(objectives);ref=frame[frame.case.eq('average')].set_index('year').objective
    frame['delta_vs_average']=frame.objective-frame.year.map(ref)
    frame['percent_vs_absolute_average']=100*frame.delta_vs_average/frame.year.map(ref).abs()
    return dict(scenario_inputs=pd.DataFrame(inputs),objective_comparison=frame,
        objective_components=pd.DataFrame(components),investments_by_technology=pd.DataFrame(investments),
        system_outcomes=pd.DataFrame(outcomes),thermal_inputs=pd.DataFrame(thermal))

def verify_national(root):
    base=root/'evidence/thermal'
    manifest=json.loads((base/'manifest.json').read_bytes())
    for name,record in manifest['files'].items():
        p=(base/name).resolve();p.relative_to(base.resolve())
        if p.stat().st_size!=record['bytes'] or sha256(p.read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Thermal evidence identity differs: '+name)
    source=json.loads((base/'source_manifest.json').read_bytes())
    if sha256((base/'national_plot_inputs.json').read_bytes()).hexdigest()!=source['data_sha256']:
        raise ValueError('Original thermal extract identity differs')
    if sha256((base/'preserved_national_audit.json').read_bytes()).hexdigest()!=source['audit_sha256']:
        raise ValueError('Original full-store audit identity differs')
    proof=json.loads((base/'preserved_national_audit.json').read_bytes())
    if proof['status']!='verified' or proof['windows']!=42 or proof['cases']!=7:
        raise ValueError('Source national audit failed')
    audited={(r['case'],r['year']):r for c in proof['evidence'] for r in c['evidence']}
    signatures={(r['case'],r['year']):r for r in source['sources']}
    rows=load(root)
    if len(rows)!=42 or len({(r['case'],r['year']) for r in rows})!=42:
        raise ValueError('Incomplete thermal cohort')
    for r in rows:
        key=r['case'],r['year'];a=audited[key]
        if a['accepted_sha256']!=signatures[key]['accepted_sha256'] or abs(a['objective_model_units']-r['obj'])>.01:
            raise ValueError('Export does not match original accepted audit')
        if max(a['constraint_violation'],a['bound_violation'])>1e-7:
            raise ValueError('Native solution quality failed')
        if abs(math.fsum(r['components'][k] for k in ACTIVE)-r['obj'])>.01:
            raise ValueError('Thermal objective components do not close')
        if abs(r['net']-r['other']-r['k'])>1e-6:
            raise ValueError('Krsko entitlement counted incorrectly')
        denominator=math.fsum(s['sum'] for s in r['input_statistics'] if
            s['family'] in ('demand','Industry_HT_demand','H2_demand','ev_Demand','cooling_demand') or
            (s['family']=='heat_demand' and s['unit'] in ('general_demand','TE-TOZagreb')))
        if abs(denominator-math.fsum(r['demands'].values()))>1e-5:
            raise ValueError('Service-demand denominator duplicates or omits a native family')
    index={(r['case'],r['year']):r for r in rows}
    for y in range(2025,2051,5):
        if not index['temperature_cold',y]['demands']['heat_demand']>index['average',y]['demands']['heat_demand']>index['temperature_hot',y]['demands']['heat_demand']:
            raise ValueError('Thermal demand ranking differs')
    return dict(planning_windows=42,new_windows=42,common_calibrated_base_year=2020,
        input_profile_hashes=42,operating_records_retained=45,
        native_annual_ledgers_audited_at_source=42,source_acceptance_hashes_verified=42,
        scope='Compact accepted thermal extract checked locally against original full-store audit; no optimizer rerun')

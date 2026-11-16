"""Reproduce current manuscript tables from the accepted annual result extract.

The discounted two-year solver objective is not relabelled as an annual cost.
No optimizer or private H2RES database is loaded by this module.
"""
from pathlib import Path
from hashlib import sha256
from math import fsum
import argparse,json,sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
YEARS=tuple(range(2025,2051,5))
CASES=('average','solar_low','solar_high','wind_low','wind_high','temperature_cold','temperature_hot')
def read(path):return json.loads(path.read_bytes())
def rows(root=ROOT):return read(root/'evidence/final/annual_results.json')['rows']
def components(r):
 c=r['calendar_costs'];op=c['operation'];cap=c['capital_payments']
 total=c['total_annual_cost']+c['one_off_soft_constraint_penalty']
 groups={'electricity_capital':fsum(cap.get(k,0.) for k in ('inv_cost','inv_cost_fossil','statCapInvCost')),
 'heating_capital':cap.get('heat_pump_cap_cost',0.)+cap.get('inv_cost_boiler',0.),
 'net_purchases':op.get('import_cost',0.),
 'heat_operation':op.get('boiler_gen_cost',0.)+op.get('ResToHeat_cost',0.)}
 groups['remaining']=total-fsum(groups.values())
 return total,groups
def capacity(r,kind):
 pairs=r['capacity_additions'].items()
 if kind=='solar':return fsum(v for k,v in pairs if k.startswith('cap_inv|HR_Solar_'))
 if kind=='wind':return fsum(v for k,v in pairs if k.startswith('cap_inv|HR_Wind_'))
 if kind=='heat_pumps':return fsum(v for k,v in pairs if k.startswith('heat_pump_') and not k.endswith('|electric_boiler'))
 raise ValueError(kind)
def annual_costs(root=ROOT):
 out=[]
 for r in rows(root):
  c=r['calendar_costs'];total,_=components(r);demand=fsum(r['annual_demand'].values())
  out.append(dict(case=r['case'],year=r['year'],year_total_EUR=total,
   capacity_terms_EUR=c['total_capital_payments'],other_active_terms_EUR=total-c['total_capital_payments'],
   annual_operation_EUR=c['total_operation'],one_off_charges_EUR=c['one_off_soft_constraint_penalty'],
   demand_MWh=demand,EUR_per_demand_MWh=total/demand))
 return pd.DataFrame(out)
def summaries(root=ROOT):
 from scripts.channel_profiles import DONORS
 from scripts.release_runtime import accepted_array
 data=rows(root);index={(r['case'],r['year']):r for r in data}
 annual=annual_costs(root);reference=annual[annual.case.eq('average')].set_index('year').year_total_EUR
 differences=annual[['case','year','year_total_EUR']].copy()
 differences['difference_million_EUR']=(differences.year_total_EUR-differences.year.map(reference))/1e6
 groups=[];additions=[];supply=[];policy=[];thermal=[];inputs=[]
 ensemble=accepted_array(root)
 for r in data:
  case,year=r['case'],r['year'];p=r['policy'];_,g=components(r);_,ref=components(index['average',year])
  for key,value in g.items():groups.append(dict(case=case,year=year,component=key,annual_EUR=value,difference_million_EUR=(value-ref[key])/1e6))
  for key,value in r['capacity_additions'].items():
   family,unit=key.split('|',1)
   additions.append(dict(case=case,year=year,family=family,technology=unit,native_unit='MWh' if family=='stat_sto_cap_inv' else 'MW',addition=value))
  policy.append(dict(case=case,year=year,net_import_TWh=p['net_import_mwh']/1e6,gross_import_TWh=p['gross_import_mwh']/1e6,
   gross_export_TWh=p['gross_export_mwh']/1e6,net_import_share=p['net_import_share'],
   new_gas_power_MW=p['new_gas_power_mw'],new_gas_boiler_MW=p['new_gas_boilers_mw'],
   power_heat_CO2_t=p['co2_power_heat_t'],power_heat_limit_t=p['co2_limit_t'],
   biomass_boiler_heat_TWh=p['biomass_boiler_heat_mwh']/1e6,
   unserved_electricity_MWh=p['shortages_mwh']['electricity'],
   unserved_individual_heat_MWh=p['shortages_mwh']['individual_heat'],
   unserved_district_heat_MWh=p['shortages_mwh']['district_heat']))
  f=r['audits'].get('fixed_demand_solution_audit',{}).get(str(year),{})
  thermal.append(dict(case=case,year=year,heat_demand_TWh=r['annual_demand']['heat_demand']/1e6,
   household_cooling_electric_TWh=f.get('household_cooling_grid_mwh',float('nan'))/1e6))
  donors=DONORS[case];profile=np.column_stack([ensemble[m,:,i] for i,m in enumerate(donors)])
  inputs.append(dict(case=case,year=year,solar_member=donors[0],wind_member=donors[1],temperature_member=donors[2],
   profile_sha256=sha256(profile.tobytes()).hexdigest()))
  if case!='average':continue
  gen=r['generation'];hydro=fsum(gen.get(k,0.) for k in ('HECombo','RHEVelebit','hror','HRSmallHydro2020'))
  wind=fsum(v for k,v in gen.items() if k.startswith('HR_Wind_'));solar=fsum(v for k,v in gen.items() if k.startswith('HR_Solar_'))
  domestic=fsum(gen.values())+r['annual_by_family'].get('generation_FC',0.)-gen.get('NEKrskoHR',0.)
  for name,value in [('Net imports including Krško entitlement',p['net_import_mwh']),('Hydro',hydro),('Wind',wind),('Solar PV',solar),('Other domestic generation',domestic-hydro-wind-solar)]:
   supply.append(dict(year=year,scope='Electricity',technology=name,TWh=value/1e6))
  for name,value in [('Gas boilers',p['gas_boiler_heat_mwh']),('Biomass boilers',p['biomass_boiler_heat_mwh']),('Other heat and heat pumps',r['annual_demand']['heat_demand']-p['gas_boiler_heat_mwh']-p['biomass_boiler_heat_mwh'])]:
   supply.append(dict(year=year,scope='Heat',technology=name,TWh=value/1e6))
 totals=pd.DataFrame([dict(case=case,**{kind+'_GW':fsum(capacity(index[case,y],kind) for y in YEARS)/1000 for kind in ('solar','wind','heat_pumps')}) for case in CASES])
 metrics=pd.read_csv(root/'results/profile_metrics.csv');mae=[]
 for channel,group in metrics.groupby('channel',sort=False):
  e=group.set_index('method').MAE
  for key,label in [('fixed_H2RES_profile','Default H2RES profile'),('training_calendar_mean','Historical mean')]:
   mae.append(dict(channel=channel,reference=label,reference_MAE=e[key],generated_MAE=e['ensemble_mean'],change_percent=100*(e['ensemble_mean']/e[key]-1)))
 crps=pd.read_csv(root/'results/manuscript/fair_crps.csv')
 op=pd.read_csv(root/'results/operational_checks.csv')
 op['HR_unserved_TWh']=op.HR_unserved_MWh/1e6;op['HR_CEEP_TWh']=op.HR_CEEP_MWh/1e6
 return {'mean_profile_MAE.csv':pd.DataFrame(mae),'fair_crps.csv':crps,
  'annual_costs.csv':annual,'annual_cost_differences.csv':differences,'annual_cost_components.csv':pd.DataFrame(groups),
  'capacity_additions.csv':pd.DataFrame(additions),'cumulative_capacity.csv':totals,
  'average_energy_supply.csv':pd.DataFrame(supply),'policy_checks.csv':pd.DataFrame(policy),
  'thermal_demand.csv':pd.DataFrame(thermal),'selected_case_inputs.csv':pd.DataFrame(inputs),
  'operating_results.csv':op}
def verify_results(root=ROOT,verify_tables=True):
 base=root/'evidence/final';manifest=read(base/'manifest.json')
 for name,info in manifest['files'].items():
  p=(base/name).resolve();p.relative_to(base.resolve())
  if p.stat().st_size!=info['bytes'] or sha256(p.read_bytes()).hexdigest()!=info['sha256']:
   raise ValueError('Final evidence differs: '+name)
 data=rows(root);proof=read(base/'completion_review.json')
 if not proof['planning_and_accounting_complete'] or proof['errors'] or proof['numerical_windows']!=42 or proof['verified_calendar_ledgers']!=42:
  raise ValueError('Incomplete original completion receipt')
 if {(r['case'],r['year']) for r in data}!={(c,y) for c in CASES for y in YEARS} or len(data)!=42:
  raise ValueError('Incomplete final planning cohort')
 for r in data:
  c=r['calendar_costs'];p=r['policy'];a=proof['paths'][r['case']][str(r['year'])]
  if abs(fsum(c['operation'].values())-c['total_operation'])>.01 or abs(fsum(c['capital_payments'].values())-c['total_capital_payments'])>.01:
   raise ValueError('Annual component closure failed')
  if abs(c['total_operation']+c['total_capital_payments']-c['total_annual_cost'])>.01:
   raise ValueError('Annual cost closure failed')
  if abs(c['total_annual_cost']-a['annual_cost'])>.01 or abs(p['net_import_mwh']/1e6-a['net_import_twh'])>1e-9:
   raise ValueError('Current rows differ from original source review')
  if abs(p['gross_import_mwh']-p['gross_export_mwh']-p['net_import_mwh'])>1e-5:
   raise ValueError('Gross and net exchange do not reconcile')
  if max(map(abs,p['shortages_mwh'].values()))>1e-7 or abs(p['new_gas_power_mw'])>1e-7 or abs(p['new_gas_boilers_mw'])>1e-7:
   raise ValueError('Coverage or gas policy failed')
  if p['co2_power_heat_t']>p['co2_limit_t']+1e-6:raise ValueError('Carbon limit exceeded')
  for item in r['audits'].get('capital_handoff_audit',{}).values():
   if item['max_surviving_capacity_residual']>1e-7:raise ValueError('State handoff residual')
  if r['audits'].get('country_exchange_solution_audit',{}).get('max_excess_mw',0)>1e-7:
   raise ValueError('Hourly infrastructure bound exceeded')
 frames=summaries(root)
 if frames['selected_case_inputs.csv'].groupby('case').profile_sha256.nunique().max()!=1:raise ValueError('Nonstationary selected profile')
 if frames['thermal_demand.csv'].household_cooling_electric_TWh.isna().any():raise ValueError('Missing cooling summary')
 if verify_tables:
  for name,actual in frames.items():
   pd.testing.assert_frame_equal(actual.reset_index(drop=True),pd.read_csv(root/'results/final'/name),check_dtype=False,rtol=1e-12,atol=1e-8,obj=name)
 return dict(planning_windows=42,planning_paths=7,common_calibrated_base_year=2020,
  annual_ledgers_checked=42,retained_operating_cases=45,unserved_national_demand_MWh=0.,
  no_new_gas_from=2025,annual_import_quota=False,
  scope='Current compact annual extract reconciled with preserved source receipt; separate original operating portfolios retained; no optimizer rerun.')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True);a=p.parse_args()
 output=Path(a.output).resolve();output.relative_to((ROOT/'exports').resolve())
 if output.exists():raise ValueError('Choose a new output directory')
 report=verify_results(ROOT)
 from analysis.legacy_manuscript_results import verify_manuscript
 report['manuscript']=verify_manuscript(ROOT);output.mkdir(parents=True)
 for name,frame in summaries(ROOT).items():frame.to_csv(output/name,index=False)
 (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,indent=2))
if __name__=='__main__':main()


"""Read-only stock, emissions and accounting audit of accepted outputs; never optimize."""
from pathlib import Path
import ast,contextlib,csv,gzip,hashlib,io,json,os,pickle,sys
R=Path(__file__).resolve().parent;P=R/'project';assert R==Path('E:/Roko/remote_access/final_frozen_profiles_20261031/multisector');os.chdir(P)
sys.path.insert(0,'E:/Roko/remote_access/energy_rebuild_20261019/runtime_packages');sys.path.insert(0,str(P))
import numpy as np,pandas as pd,gurobipy as gp
def forbidden(*a,**k):raise RuntimeError('No optimizer may be created in this read-only audit')
gp.Model=forbidden
ctx={'__name__':'accounting_read_only','__file__':str(P/'main_rolling_horizon_v3.py')}
tree=ast.parse((P/'main_rolling_horizon_v3.py').read_text());tree.body=tree.body[:-2]
with contextlib.redirect_stdout(io.StringIO()):exec(compile(tree,'native_configuration','exec'),ctx)
tree=ast.parse((P/'scripts/modeling_approaches.py').read_text());stop=next(i for i,n in enumerate(tree.body) if isinstance(n,ast.If) and 'Rolling_Horizon' in ast.unparse(n.test));tree.body=tree.body[:stop]
with contextlib.redirect_stdout(io.StringIO()):exec(compile(tree,'native_read_process_only','exec'),ctx)
class DataOnly(pickle.Unpickler):
 def find_class(self,module,name):
  if module=='numpy' and name=='dtype':return np.dtype
  if module in ('numpy.core.multiarray','numpy._core.multiarray') and name=='scalar':return np.core.multiarray.scalar
  raise pickle.UnpicklingError(module+'.'+name)
def readstate(p):
 with p.open('rb') as f:return DataOnly(f).load()['state']
def write(name,v):
 (R/name).write_text(json.dumps(v,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else str(x)),encoding='utf8')
Y=list(range(2020,2051,5));checks=[];stock=[];revisions=[];account=[];emis=[];constraint_checks=[]
families={'cap_inv_cumulative':('cap_inv','decom_rate','decom_start_new'),'cap_inv_FC_cumulative':('cap_inv_FC',None,None),'heat_pump_dh_cap_cumulative':('heat_pump_dh_cap','HP_decom_rate','HP_decom_start_new'),'heat_pump_ind_cap_cumulative':('heat_pump_ind_cap','HP_decom_rate','HP_decom_start_new'),'trad_boiler_ind_cap_inv_cumulative':('trad_boiler_ind_cap_inv','Boiler_decom_rate','Boiler_decom_start_new'),'trad_boiler_dh_cap_inv_cumulative':('trad_boiler_dh_cap_inv','Boiler_decom_rate','Boiler_decom_start_new'),'stat_sto_cap_inv_cumulative':('stat_sto_cap_inv','Sta_sto_decom_rate','Sta_sto_decom_start_new'),'h2_sto_max_cap_cumulative':('h2_sto_max_cap',None,None),'h2_electrolysis_max_cap_cumulative':('h2_electrolysis_max_cap',None,None)}
for case in ['avg','low','high']:
 folder=R/'jobs'/(case+'_path');realized={};prior=None
 for y in Y:
  out=folder/str(y);result=json.loads((out/'solver_result.json').read_text());pre=readstate(out/f'inherited_{y}.pkl');current={}
  for r in result['capacity_decisions']:
   if int(r['key'][-1])==y:current[r['family'],tuple(r['key'])]=r['value']
  if prior is not None:
   for key,actual in current.items():
    former=prior.get(key,0.)
    if abs(actual-former)>1e-6:revisions.append({'case':case,'year':y,'family':key[0],'key':key[1],'previous_provisional':former,'realized':actual})
  for name,(family,rate,start) in families.items():
   for key,saved in pre[name].items():
    if key[-1]!=y:continue
    expected=0.
    for (f,k),v in realized.items():
     if f!=family or k[:-1]!=key[:-1] or k[-1]>=y:continue
     factor=round((1-ctx[rate][key[0]])**(max(ctx[start][key[0]]-1,y-k[-1])-(ctx[start][key[0]]-1)),3) if rate else 1.
     expected+=v*factor
    gap=float(saved)-expected
    checks.append({'case':case,'year':y,'family':family,'key':key,'gap':gap,'passed':abs(gap)<1e-6})
    addition=current.get((family,key),0.);old=0.
    if family=='cap_inv':old=ctx['max_load'][key[0]]*round((1-ctx['decom_rate'][key[0]])**(max(ctx['decom_start_old'][key[0]]-1,y-2020)-(ctx['decom_start_old'][key[0]]-1)),3)
    elif family=='trad_boiler_ind_cap_inv':old=ctx['Thermal_ind_init_cap'][key[0]]*ctx['Thermal_Decom_ind'][y]
    elif family=='heat_pump_ind_cap':old=ctx['HP_ind_init_cap'][key[0]]*ctx['HP_Decom_ind'][y]
    elif family in ('heat_pump_dh_cap','trad_boiler_dh_cap_inv'):old=ctx['HP_Boiler_dh_init_cap'][key[:-1]]*(ctx['HP_Decom_ind'][y] if family=='heat_pump_dh_cap' else ctx['Thermal_Decom_DH'][y])
    elif family=='cap_inv_FC':old=ctx['FC_init_cap'][key[0]]
    elif family=='stat_sto_cap_inv':old=ctx['Sta_sto_init_cap'][key[0]]*ctx['StaSto_Decom_ind'][y]
    elif family=='h2_sto_max_cap':old=ctx['H2_sto_init'][key[0]]
    elif family=='h2_electrolysis_max_cap':old=ctx['H2_init_cap'][key[0]]
    stock.append({'case':case,'year':y,'family':family,'key':key,'remaining_original':old,'inherited_new':float(saved),'new_addition':addition,'installed_native_capacity':old+float(saved)+addition})
  realized.update(current);prior={(r['family'],tuple(r['key'])):r['value'] for r in result['capacity_decisions']}
  demands={};selected_heat=set(ctx['chp_units'])|{'general_demand'}
  with gzip.open(out/'runtime_accounting_inputs.csv.gz','rt',encoding='utf8') as f:
   for r in csv.DictReader(f):
    key=json.loads(r['key_json']);family=r['family'];yy=int(key[-1]);value=float(r['value'])
    if family=='heat_demand' and key[0] not in selected_heat:continue
    if family in ['demand','heat_demand','cooling_demand','Industry_HT_demand','H2_demand']:demands[family,yy]=demands.get((family,yy),0.)+value
  led=json.loads((out/'annual_objective_ledger.json').read_text());weighted=0.;terms=[]
  for yy in result['window_years']:
   ev=float(sum(v for (p,ey),v in ctx['ev_transp_load'].items() if ey==yy)*ctx['ev_Demand'][yy])
   native=sum(v for (f,ey),v in demands.items() if ey==yy)+ev;weighted+=ctx['NPV'][yy]*native
   terms.append({'year':yy,'NPV':ctx['NPV'][yy],'demand_MWh_by_family':{f:v for (f,ey),v in demands.items() if ey==yy},'ev_transport_MWh':ev,'native_demand_sum_MWh':native,'objective_contribution':led['values_by_anchor'][str(yy)]})
  account.append({'case':case,'window_start':y,'objective':result['objective'],'weighted_native_service_MWh':weighted,'objective_intensity_model_units_per_weighted_MWh':result['objective']/weighted,'terms':terms,'not_LCOE':True})
  annual=result['annual_sums']
  for yy in result['window_years']:
   def get(fam,unit=None):
    return sum(v for label,v in annual.items() for f,k in [label.split('|',1)] for key in [json.loads(k)] if f==fam and key[-1]==yy and (unit is None or key[0]==unit))
   expected_power=sum(ctx['CO2_factor'][u]*get('generation',u) for u in ctx['fossil_units']);expected_heat=sum(ctx['Thermal_CO2'][u]*(get('boiler_ind_generation',u)+get('boiler_dh_generation',u)) for u in ctx['thermal_units']);expected_ind=sum(ctx['Fuel_CO2'][u]*get('Fuel_ind',u) for u in ctx['industry_fuels'])
   for family,expected in [('CO2_total',expected_power),('CO2_total_heat',expected_heat),('CO2_total_industry',expected_ind)]:
    saved=get(family);emis.append({'case':case,'window_start':y,'year':yy,'family':family,'saved_tCO2':saved,'reconstructed_tCO2':expected,'gap_tCO2':saved-expected,'passed':abs(saved-expected)<.01})
  # Independently inspect native demand-balance equalities, preserving their identities.
  selected={'meet_demand','meet_heat_demand','meet_heat_demand_gen','meet_cooling_demand_gen','meet_cooling_demand_dh','industry_demand_meet','h2_demand'};counts={};maxima={}
  with gzip.open(out/'keyed_constraints.csv.gz','rt',encoding='utf8') as f:
   for row in csv.DictReader(f):
    if row['family'] in selected:
     name=row['family'];counts[name]=counts.get(name,0)+1;maxima[name]=max(maxima.get(name,0.),abs(float(row['slack'])))
  constraint_checks.append({'case':case,'year':y,'counts':counts,'max_absolute_balance_slack':maxima,'passed':bool(counts) and max(maxima.values())<=1e-7})
  write('post_audit_progress.json',{'case':case,'year':y,'processed_windows':len(account)})
write('post_audit.json',{'solver_started':False,'stock_checks':checks,'stock_all_passed':all(r['passed'] for r in checks),'provisional_revisions':revisions,'installed_capacity':stock,'accounting':account,'emission_checks':emis,'emissions_all_passed':all(r['passed'] for r in emis),'balance_checks':constraint_checks,'balances_all_passed':all(r['passed'] for r in constraint_checks),'denominator_scope':'Model native exogenous electricity, selected general/district heat, cooling, Industry_HT fuel-equivalent demand, external H2 and EVtransport flows. Internal conversion/storage flows excluded; heat_demand Industry_HT column excluded to avoid duplicating Industry_HT_demand. A mixed model-demand cost intensity, not LCOE or a verified currency/year measure.'})

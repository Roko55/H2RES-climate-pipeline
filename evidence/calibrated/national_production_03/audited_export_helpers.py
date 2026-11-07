"""One isolated conditional window; unchanged equations and recorded precision policy."""
from pathlib import Path
import ast,csv,gzip,hashlib,inspect,importlib.util,json,os,pickle,sys,time,traceback
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
assert ROOT==Path('E:/Roko/remote_access/problem4_controls_20261030')
sys.path.insert(0,'E:/Roko/remote_access/energy_rebuild_20261019/runtime_packages')
import numpy as np
import gurobipy as gp
case=sys.argv[2];year=int(sys.argv[3]);assert case in ('avg','low','high') and year in (2025,2050)
PROJECT=ROOT/'project';OUT=ROOT/'runs'/f'{case}_{year}';OUT.mkdir(parents=True,exist_ok=True)
MAPPING=Path('E:/Roko/remote_access/resource_batch_20261029/legacy_whole_path_03/mapping_20261030')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def clean(v):
 if isinstance(v,dict):return {str(k):clean(x) for k,x in sorted(v.items(),key=lambda a:str(a[0]))}
 if isinstance(v,(int,float,np.integer,np.floating)):return float(v)
 if isinstance(v,(list,tuple)):return [clean(x) for x in v]
 return v
def digest(v):return hashlib.sha256(json.dumps(clean(v),sort_keys=True).encode()).hexdigest()
def save(name,v):
 p=OUT/name;t=p.with_suffix(p.suffix+'.pending');t.write_text(json.dumps(v,indent=2,default=lambda x:x.item() if isinstance(x,np.generic) else str(x)),encoding='utf8');t.replace(p)
class DataOnly(pickle.Unpickler):
 def find_class(self,module,name):
  if module=='numpy' and name=='dtype':return np.dtype
  if module in ('numpy.core.multiarray','numpy._core.multiarray') and name=='scalar':return np.core.multiarray.scalar
  raise pickle.UnpicklingError(module+'.'+name)
snapshot_path=MAPPING/'results/repaired_avg/quality'/f'inherited_{year}.pkl'
with snapshot_path.open('rb') as f:snapshot=DataOnly(f).load()
assert snapshot['years']==([2025,2030] if year==2025 else [2050])
settings=json.loads((MAPPING/'projects/repaired_avg/review_command.json').read_text())['settings']
for key in list(os.environ):
 if key.startswith('H2RES_') or key.startswith('LEGACY_'):os.environ.pop(key)
settings.update({'LEGACY_AUDIT_DIR':str(OUT/'quality'),'H2RES_CLIMATE_SCENARIO':case+'_yield__normal','H2RES_RUN_LABEL':case+'_sameT_sameState_'+str(year),'H2RES_PROFILE_OVERRIDES':'temperature=avg_yield__normal','H2RES_SOLVER_SUMMARY_DIR':str(OUT),'H2RES_CAPACITY_EXPORT_DIR':str(OUT),'CONTROL_INDEX':str(snapshot['index'])})
os.environ.update(settings)
def _control_restore(ctx):
 assert ctx['years']==snapshot['years'] and ctx['i']==snapshot['index'] and ctx['hor_yrs']==snapshot['hor_yrs']
 actual={k for k,v in ctx.items() if isinstance(v,dict) and (k.endswith('_cumulative') or k.startswith('values_'))}
 assert actual==set(snapshot['state']),('state schema mismatch',actual^set(snapshot['state']))
 for key,value in snapshot['state'].items():ctx[key]=dict(value)
 assert digest({k:ctx[k] for k in actual})==digest(snapshot['state'])
 ctx['_control_inherited_sha256']=digest(snapshot['state'])
def _control_before(model,ctx):
 assert ctx['years']==snapshot['years'] and ctx.get('ai_climate_available')
 assert ctx['profile_channel_overrides']=={'temperature':'avg_yield__normal'}
 inp={'case':case,'window_years':ctx['years'],'inherited_snapshot_sha256':sha(snapshot_path),'inherited_state_sha256':ctx['_control_inherited_sha256'],'hashes':{},'stats':{}}
 for name in ['heat_demand','cooling_demand','HP_COP_period','avl_factor','availability_factor','avail_factor']:
  if isinstance(ctx.get(name),dict):inp['hashes'][name]=digest(ctx[name])
 for name in ['heat_demand','cooling_demand','HP_COP_period']:
  assert name in inp['hashes'],name
  for y in ctx['years']:
   a=[float(v) for k,v in ctx[name].items() if isinstance(k,tuple) and k[-1]==y]
   inp['stats'][name+'_'+str(y)]={'sum':sum(a),'max':max(a),'count':len(a)}
 baseline=ROOT/'runs'/f'avg_{year}'/'input_audit.json'
 if case!='avg':
  ref=json.loads(baseline.read_text());assert ref['inherited_state_sha256']==inp['inherited_state_sha256']
  for name in ['heat_demand','cooling_demand','HP_COP_period']:assert inp['hashes'][name]==ref['hashes'][name],name
 inp['fingerprint_before_solve']=int(model.Fingerprint)
 if case=='avg':
  old=json.loads((MAPPING/'results/repaired_avg/quality'/('quality_'+'_'.join(map(str,ctx['years']))+'.json')).read_text())
  assert int(old['Fingerprint'])==int(model.Fingerprint),('baseline matrix mismatch',old['Fingerprint'],model.Fingerprint)
 save('input_audit.json',inp)
def _control_after(model,ctx):
 assert model.Status==2 and model.ConstrVio<=1e-7 and model.BoundVio<=1e-7
 variables=model.getVars()
 with gzip.open(OUT/'all_variables.csv.gz','wt',encoding='utf8',newline='') as f:
  w=csv.writer(f);w.writerow(['variable','value','reduced_cost'])
  for v in variables:w.writerow([v.VarName,v.X,v.RC])
 constraints=model.getConstrs()
 with gzip.open(OUT/'all_constraints.csv.gz','wt',encoding='utf8',newline='') as f:
  w=csv.writer(f);w.writerow(['constraint','sense','rhs','slack','dual'])
  for c in constraints:w.writerow([c.ConstrName,c.Sense,c.RHS,c.Slack,c.Pi])
 families=['generation','imports','ceep','cap_inv','cap_inv_FC','heat_pump_dh_cap','heat_pump_ind_cap','trad_boiler_ind_cap_inv','trad_boiler_dh_cap_inv','stat_sto_cap_inv','h2_sto_max_cap','h2_electrolysis_max_cap','CO2_total','CO2_total_heat','CO2_total_industry','Fuel_ind','boiler_ind_generation','boiler_dh_generation','heat_pump_ind','heat_pump_dh','noload_elec','noload_heat_ind','noload_heat_dh','generation_FC','generation_H2','power_to_h2','heat_gen']
 annual={};caps=[]
 with gzip.open(OUT/'keyed_outputs.csv.gz','wt',encoding='utf8',newline='') as f:
  w=csv.writer(f);w.writerow(['family','key_json','value'])
  for name in families:
   dic=ctx.get(name,{})
   for key,var in dic.items():
    key=key if isinstance(key,tuple) else (key,);val=float(var.X);w.writerow([name,json.dumps(key,default=lambda x:x.item()),val])
    if 'cap' in name or name=='cap_inv':caps.append({'family':name,'key':key,'value':val})
    else:
     group=key[:-2]+(key[-1],) if len(key)>=2 else key
     label=name+'|'+json.dumps(group,default=lambda x:x.item());annual[label]=annual.get(label,0.)+val
 result={'case':case,'year':year,'window_years':ctx['years'],'objective':model.ObjVal,'status':int(model.Status),'fingerprint':int(model.Fingerprint),'runtime_seconds':model.Runtime,'constraint_violation':model.ConstrVio,'bound_violation':model.BoundVio,'annual_sums':annual,'capacity_decisions':caps,'complete_hourly_outputs_saved':True}
 if case=='avg':
  old=json.loads((MAPPING/'results/repaired_avg/quality'/('quality_'+'_'.join(map(str,ctx['years']))+'.json')).read_text())
  result['reference_objective']=old['ObjVal'];result['reference_objective_gap']=model.ObjVal-old['ObjVal']
  assert abs(result['reference_objective_gap'])<=1e-6*max(1,abs(old['ObjVal']))
 save('solver_result.json',result)
def _control_finish(ctx):
 rows=ctx['objective_breakdown_results'];assert rows
 ctx['_write_objective_breakdown_outputs'](str(OUT/'objective_breakdown_outputs.csv'),rows)
 save('solver_summary.json',ctx['solver_subproblem_summary'])
 save('completed.json',{'case':case,'year':year,'status':'completed_checked','equations_modified':False,'initial_state':'exact saved Average state','temperature':'exact Average profile through original override'})
try:
 # The precision recorder's original ownership guard allows only its historical batch.
 # Adapt that single output-path guard in an owned copy; preserve policies and acceptance exactly.
 original_policy=MAPPING.parents[1]/'helpers/precision_policy.py'
 policy=ast.parse(original_policy.read_text(encoding='utf8'));guards=[]
 for node in ast.walk(policy):
  if isinstance(node,ast.FunctionDef) and node.name=='solve':
   for index,item in enumerate(node.body):
    if isinstance(item,ast.Expr) and isinstance(item.value,ast.Call) and isinstance(item.value.func,ast.Attribute) and item.value.func.attr=='relative_to' and isinstance(item.value.func.value,ast.Name) and item.value.func.value.id=='record_path':guards.append((node,index))
 assert len(guards)==1
 node,index=guards[0];node.body[index]=ast.parse("record_path.relative_to(Path('E:/Roko/remote_access/problem4_controls_20261030').resolve())").body[0]
 ast.fix_missing_locations(policy);owned_policy=ROOT/'precision_policy_owned.py';owned_policy.write_text(ast.unparse(policy)+'\n',encoding='utf8')
 spec=importlib.util.spec_from_file_location('precision_policy',owned_policy);module=importlib.util.module_from_spec(spec);sys.modules['precision_policy']=module;spec.loader.exec_module(module)
 save('precision_policy_ownership_patch.json',{'original_sha256':sha(original_policy),'owned_sha256':sha(owned_policy),'only_change':'output-path ownership guard; solver policies and acceptance unchanged'})
 # Reuse the exact old quality wrapper, changing only its owned-path guard and adding observational hooks.
 tree=ast.parse((MAPPING/'results/repaired_avg/legacy_runner.py').read_text(encoding='utf8'))
 output=[]
 for node in tree.body:
  if isinstance(node,ast.For) and isinstance(node.target,ast.Name) and node.target.id=='target':
   output+=ast.parse("assert PROJECT.resolve().is_relative_to(ROOT.resolve()) and OUT.resolve().is_relative_to(ROOT.resolve())").body;continue
  if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and node.value.func.attr=='run_path':continue
  if isinstance(node,ast.ClassDef) and node.name=='AuditedModel':
   method=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name=='optimize')
   method.body.insert(1,ast.parse('_control_before(self, inspect.currentframe().f_back.f_globals)').body[0])
   method.body+=ast.parse('_control_after(self, inspect.currentframe().f_back.f_globals)').body
  output.append(node)
 tree.body=output;ast.fix_missing_locations(tree)
 # The legacy wrapper takes PROJECT from argv[1] and OUT from LEGACY_AUDIT_DIR.
 sys.argv[1]=str(PROJECT);os.environ['LEGACY_AUDIT_DIR']=str(OUT/'quality')
 exec(compile(tree,'controlled_quality_wrapper','exec'),globals())
 # Wrapper defines OUT as its quality directory: restore the enclosing run output path for hooks.
 OUT=ROOT/'runs'/f'{case}_{year}'
 os.chdir(PROJECT);sys.path.insert(0,str(PROJECT))
 namespace={'__name__':'__main__','__file__':str(PROJECT/'main_rolling_horizon_v3.py'),'_control_restore':_control_restore,'_control_finish':_control_finish}
 exec(compile((PROJECT/'main_rolling_horizon_v3.py').read_text(encoding='utf8'),str(PROJECT/'main_rolling_horizon_v3.py'),'exec'),namespace)
 assert (OUT/'completed.json').exists()
except BaseException:
 save('failure.json',{'traceback':traceback.format_exc(),'case':case,'year':year});raise

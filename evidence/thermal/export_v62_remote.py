"""Read-only extraction of audited thermal production; never import an optimizer."""
from pathlib import Path
from hashlib import sha256
from math import fsum,isclose
import csv,gzip,json,time,traceback

BASE=Path('E:/Roko/remote_access')
ROOT=BASE/'manuscript_v62_export_20261108'
NATIONAL=BASE/'national_production_thermal_20261107_01'
RECOVERY=BASE/'thermal_controls_recovery_20261108_01'
CASES=('average','solar_low','solar_high','wind_low','wind_high','temperature_cold','temperature_hot')
YEARS=tuple(range(2025,2051,5))

def load(path):return json.loads(path.read_bytes())
def digest(path):return sha256(path.read_bytes()).hexdigest()
def save(name,value):
    temp=ROOT/(name+'.pending');temp.write_text(json.dumps(value,allow_nan=False,separators=(',',':'))+'\n')
    temp.replace(ROOT/name)

def main():
    if Path(__file__).resolve().parent!=ROOT:raise ValueError('Wrong export directory')
    proof=load(RECOVERY/'preserved_national_audit.json')
    if proof['status']!='verified' or proof['windows']!=42:raise ValueError('No completed national audit')
    accepted_index={(r['case'],r['year']):r['accepted_sha256']
        for case in proof['evidence'] for r in case['evidence']}
    output=[];sources=[]
    for case in CASES:
        for year in YEARS:
            folder=NATIONAL/'jobs'/case/str(year);marker=folder/'accepted.json'
            if digest(marker)!=accepted_index[case,year]:raise ValueError('Accepted marker changed')
            accepted=load(marker)
            names=('solver_result.json','annual_objective_ledger.json','investment_terms.csv',
                'objective_breakdown_outputs.csv','runtime_accounting_inputs.csv.gz')
            hashes={name:digest(folder/name) for name in names}
            if any(hashes[n]!=accepted['files'][n] for n in names):raise ValueError('Native data changed')
            solver=load(folder/'solver_result.json');ledger=load(folder/'annual_objective_ledger.json')
            if solver['status']!=2 or max(solver['constraint_violation'],solver['bound_violation'])>1e-7:
                raise ValueError('Unaccepted national solution')
            if abs(fsum([*ledger['values_by_anchor'].values(),ledger['constant'],ledger['unassigned'],-solver['objective']]))>.01:
                raise ValueError('Objective ledger does not close')
            with (folder/'investment_terms.csv').open(encoding='utf8',newline='') as f:terms=list(csv.DictReader(f))
            realized=[r for r in terms if int(r['realized'])==1 and int(r['decision_year'])==year]
            weight=ledger['NPV'][str(year)]
            investment=fsum(float(r['objective_contribution']) for r in realized)/weight
            cap={}
            for r in realized:
                if int(r['capacity_addition']) and abs(float(r['value']))>1e-7:
                    key=r['family']+'|'+str(json.loads(r['key_json'])[0]);cap[key]=cap.get(key,0.)+float(r['value'])
            with (folder/'objective_breakdown_outputs.csv').open(encoding='utf8',newline='') as f:parts=list(csv.DictReader(f))
            components={}
            for r in parts:
                g=r['component_group'];components[g]=components.get(g,0.)+float(r['value'])
            if abs(fsum(float(r['value']) for r in parts if int(r['active_in_objective'])==1)-solver['objective'])>.01:
                raise ValueError('Active objective components do not close')
            demand_values={k:[] for k in ('demand','heat_demand','cooling_demand','Industry_HT_demand','H2_demand','ev_Demand')}
            heat_values={};statistics={}
            with gzip.open(folder/'runtime_accounting_inputs.csv.gz','rt',encoding='utf8',newline='') as f:
                for r in csv.DictReader(f):
                    keys=json.loads(r['key_json']);family=r['family']
                    if int(keys[-1])!=year:continue
                    v=float(r['value']);unit=str(keys[0]) if family in ('heat_demand','cooling_demand','HP_COP_period') else 'all'
                    statistics.setdefault((family,unit),[]).append(v)
                    if family=='heat_demand':heat_values.setdefault(unit,[]).append(v)
                    if family in demand_values and (family!='heat_demand' or unit in ('general_demand','TE-TOZagreb')):
                        demand_values[family].append(v)
            demands={k:fsum(v) for k,v in demand_values.items()}
            sums={k:v for k,v in solver['annual_sums'].items() if int(json.loads(k.split('|',1)[1])[-1])==year}
            generation={}
            for k,v in sums.items():
                if k.split('|',1)[0]=='generation':
                    unit=str(json.loads(k.split('|',1)[1])[0]);generation[unit]=generation.get(unit,0.)+v
            kr=fsum(v for k,v in sums.items() if k.split('|',1)[0]=='generation' and json.loads(k.split('|',1)[1])[0]=='NEKrskoHR')
            imports=sums[f'imports|[{year}]']
            row=dict(case=case,year=year,inv=investment,k=kr,other=imports,net=kr+imports,
                obj=solver['objective'],demands=demands,heat_demands={k:fsum(v) for k,v in heat_values.items()},
                year_total=ledger['values_by_anchor'][str(year)]/weight,components=components,cap=cap,
                generation=generation,supply=sums,
                input_statistics=[dict(family=k[0],unit=k[1],count=len(v),sum=fsum(v),minimum=min(v),maximum=max(v)) for k,v in statistics.items()])
            output.append(row);sources.append(dict(case=case,year=year,accepted_sha256=digest(marker),files_sha256=hashes))
            save('status.json',dict(stage='extracting',completed=len(output),total=42,new_optimization=False))
    save('national_plot_inputs.json',dict(national=output))
    save('source_manifest.json',dict(status='verified_against_accepted_thermal_production',sources=sources,
        audit_sha256=digest(RECOVERY/'preserved_national_audit.json'),
        exporter_sha256=digest(Path(__file__)),data_sha256=digest(ROOT/'national_plot_inputs.json'),
        national_root=str(NATIONAL),new_optimization=False,operating_tests_exported=False))
    save('status.json',dict(stage='completed_verified',completed=42,total=42,new_optimization=False,
        data_bytes=(ROOT/'national_plot_inputs.json').stat().st_size,
        data_sha256=digest(ROOT/'national_plot_inputs.json'),manifest_sha256=digest(ROOT/'source_manifest.json')))

if __name__=='__main__':
    try:main()
    except BaseException:
        save('status.json',dict(stage='failed',error=traceback.format_exc(),new_optimization=False));raise

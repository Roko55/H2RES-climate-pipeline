"""Recreate the ten current national-result figures with the retained design."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from math import fsum
import inspect,json,argparse
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from analysis.final_results import ROOT,verify_results
from analysis.final_plot_context import source,raw
from analysis import final_plot_style as plotter
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',required=True);args=parser.parse_args()
output=Path(args.output).resolve();output.relative_to((ROOT/'exports').resolve())
if output.exists():raise ValueError('Choose a new output directory')
verify_results(ROOT);output.mkdir(parents=True)
rows=[]
for r in raw['rows']:
    costs=r['calendar_costs'];op=costs['operation'];cap=costs['capital_payments']
    total=costs['total_annual_cost']+costs['one_off_soft_constraint_penalty']
    components={
        'generation_investment_cost':fsum(cap.get(k,0.) for k in ('inv_cost','inv_cost_fossil','statCapInvCost')),
        'heat_sector_investment_cost':cap.get('heat_pump_cap_cost',0.)+cap.get('inv_cost_boiler',0.),
        'optimized_import_cost':op.get('import_cost',0.),
        'heat_sector_operation_cost':op.get('boiler_gen_cost',0.)+op.get('ResToHeat_cost',0.),
    }
    components['remaining']=total-fsum(components.values())
    assert abs(fsum(components.values())-total)<1e-5
    rows.append(dict(case=r['case'],year=r['year'],obj=total,year_total=total,
        inv=costs['total_capital_payments'],components=components,cap=r['capacity_additions'],
        generation=r['generation'],demands=r['annual_demand'],policy=r['policy'],
        supply=r['annual_by_family']))
source.DATA=dict(national=rows);source.ROWS={(r['case'],r['year']):r for r in rows}
source.AVERAGE=[source.ROWS['average',y] for y in source.YEARS]
plotter.OUT=output;plotter.OUT.mkdir(exist_ok=True);plotter.manifest={}
plotter.LABELS=[str(y) for y in source.YEARS]
code=inspect.getsource(plotter.comparison)
inset="""        if channel=='temperature' and c==cases[0]:
            zax=ax.inset_axes([.48,.12,.49,.48]);bars(zax,c,np.arange(1,N))
            zax.set_xticks(Y[1:]);zax.tick_params(labelsize=6);zax.set_title('2030–2050 enlarged',fontsize=7)
"""
assert code.count(inset)==1
code=code.replace(inset,'').replace('Planning window','Planning year')
code=code.replace("if channel=='temperature':",'if True:')
code=code.replace("'Generation investment'","'Electricity capital'").replace("'Heating investment'","'Heating capital'")
code=code.replace("'Imports'","'Net purchases'")
exec(compile(code,'preserved_comparison_annual_costs','exec'),plotter.__dict__)
for channel,cases,labels,indices in (
 ('solar',('solar_low','solar_high'),('Low','High'),(88,90)),
 ('wind',('wind_low','wind_high'),('Low','High'),(95,97)),
 ('temperature',('temperature_cold','temperature_hot'),('Cold','Warm'),(102,104))):
    plotter.comparison(channel,cases,labels,indices)

Y=np.array(source.YEARS);BLUE,ORANGE,DARK=plotter.BLUE,plotter.ORANGE,plotter.DARK
fig,axes=plt.subplots(2,1,figsize=(8.5,5.1),sharex=True)
total=np.array([r['year_total'] for r in source.AVERAGE]);capital=np.array([r['inv'] for r in source.AVERAGE])
axes[0].bar(Y,(total-capital)/1e6,width=3,color=BLUE,label='Operation and other charges')
axes[0].bar(Y,capital/1e6,bottom=(total-capital)/1e6,width=3,color=ORANGE,label='Annual capital payments')
for y,z in zip(Y,total/1e6):axes[0].annotate(f'{z:.0f}',(y,z),xytext=(0,5),textcoords='offset points',ha='center',fontsize=8)
axes[0].set_ylabel('Million EUR / year');axes[0].legend(ncol=2,fontsize=8);axes[0].margins(y=.25)
intensity=total/np.array([fsum(r['demands'].values()) for r in source.AVERAGE])
axes[1].plot(Y,intensity,'o-',c=DARK)
for y,z in zip(Y,intensity):axes[1].annotate(f'{z:.2f}',(y,z),xytext=(0,7),textcoords='offset points',ha='center',fontsize=8)
axes[1].set(ylabel='EUR / demand MWh',xlabel='Realized decision year',xticks=Y);axes[1].margins(y=.3)
for ax in axes:ax.grid(axis='y',alpha=.12);ax.set_axisbelow(True)
fig.tight_layout();plotter.save(110,fig)

def pv(r):return fsum(v for k,v in r['cap'].items() if k.startswith('cap_inv|HR_Solar_'))
def wind(r):return fsum(v for k,v in r['cap'].items() if k.startswith('cap_inv|HR_Wind_'))
def hp(r):return fsum(v for k,v in r['cap'].items() if k.startswith('heat_pump_') and not k.endswith('|electric_boiler'))
def eb(r):return fsum(v for k,v in r['cap'].items() if k.startswith('heat_pump_') and k.endswith('|electric_boiler'))
fig,axes=plt.subplots(2,1,figsize=(8.5,5.8),sharex=True)
electricity=[('Solar PV',pv,'#eab308'),('Wind',wind,'#0d9488'),('Battery',lambda r:.225*r['cap'].get('stat_sto_cap_inv|Liion_storage',0.),BLUE)]
heating=[('Heat pumps',hp,BLUE),('Electric boiler',eb,ORANGE)]
for ax,title,items in zip(axes,('Electricity','Heating'),(electricity,heating)):
    bottom=np.zeros(len(Y))
    for label,get,color in items:
        values=np.array([get(r) for r in source.AVERAGE]);ax.bar(Y,values,bottom=bottom,width=3,color=color,label=label);bottom+=values
    for y,v in zip(Y,bottom):
        if v>1e-5:ax.annotate(f'{v:.0f}',(y,v),xytext=(0,5),textcoords='offset points',ha='center',fontsize=8)
    ax.set(ylabel='New capacity (MW)',xticks=Y);ax.set_title(title,pad=35)
    ax.legend(ncol=3,fontsize=7.5,loc='lower left',bbox_to_anchor=(0,1.01),frameon=False)
    ax.set_ylim(0,max(bottom)*1.32);ax.grid(axis='y',alpha=.12);ax.set_axisbelow(True)
axes[-1].set_xlabel('Realized decision year');fig.tight_layout(h_pad=1.5);plotter.save(114,fig)

def annual_sum(r,family,unit=None):
    if unit is None:return r['supply'].get(family,0.)
    # Gas/biomass/oil individual heat is read from audited solver annual sums below.
    return r['heat_by_unit'].get(family+'|'+unit,0.)

# Use total heat demand and the policy's audited combined gas/biomass heat.
power={k:[] for k in ('Net imports','Other domestic generation','Hydro','Wind','Solar PV')}
heat={k:[] for k in ('Gas boilers','Biomass boilers','Other heat and heat pumps')}
for r in source.AVERAGE:
    g=r['generation'];hydro=fsum(g.get(k,0.) for k in ('HECombo','RHEVelebit','hror','HRSmallHydro2020'))
    w=fsum(v for k,v in g.items() if k.startswith('HR_Wind_'));s=fsum(v for k,v in g.items() if k.startswith('HR_Solar_'))
    kr=g.get('NEKrskoHR',0.);domestic=fsum(g.values())+r['supply'].get('generation_FC',0.)-kr
    for name,value in zip(power,(r['policy']['net_import_mwh'],domestic-hydro-w-s,hydro,w,s)):power[name].append(value/1e6)
    gas=r['policy']['gas_boiler_heat_mwh'];bio=r['policy']['biomass_boiler_heat_mwh'];delivered=r['demands']['heat_demand']
    for name,value in zip(heat,(gas,bio,delivered-gas-bio)):heat[name].append(value/1e6)
fig,axes=plt.subplots(2,1,figsize=(8.5,7))
plotter.hstacks(axes[0],power,['#334155','#a855f7','#38bdf8','#0d9488','#eab308'],'Electricity supply','TWh in the displayed year')
plotter.hstacks(axes[1],heat,[ORANGE,'#65a30d',BLUE],'General and district heat','TWh in the displayed year')
fig.tight_layout(h_pad=3);plotter.save(118,fig)

cases=('average','solar_low','solar_high','wind_low','wind_high','temperature_cold','temperature_hot')
labels=('Average','Solar Low','Solar High','Wind Low','Wind High','Cold','Warm')
fig,axes=plt.subplots(1,3,figsize=(8.5,3.5),sharey=True)
for ax,title,get,color,unit in zip(axes,('Solar PV','Wind','Heat pumps'),(pv,wind,hp),('#eab308','#0d9488',BLUE),('GW electric','GW electric','GW heat')):
    values=np.array([fsum(get(source.ROWS[c,y]) for y in source.YEARS)/1000 for c in cases])
    ax.barh(range(len(cases)),values,color=color,height=.6)
    for i,value in enumerate(values):ax.text(value+max(values)*.025,i,f'{value:.2f}',va='center',fontsize=8)
    ax.set_title(title,pad=12);ax.set_xlabel(unit);ax.set_yticks(range(len(cases)),labels);ax.set_xlim(0,max(values)*1.24)
    ax.grid(axis='x',alpha=.1);ax.set_axisbelow(True)
axes[0].invert_yaxis();fig.tight_layout(w_pad=1.4);plotter.save(120,fig)

(output/'figures.json').write_text(json.dumps(plotter.manifest,indent=2))
print(json.dumps({'current_figures':len(plotter.manifest),'output':str(output)}))

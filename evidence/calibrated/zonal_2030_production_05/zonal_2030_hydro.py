"""Wire absolute ERAA water resources to the three-zone native hydro equations."""

import csv
from hashlib import sha256
from io import StringIO
import json
from math import fsum, isclose, isfinite


HOURS = tuple(range(1,8761))
YEAR = 2030
TOL = 1e-7
SOURCE_SHA = '2a5503b50c6a8346954f5cb4f6a53cfb08ccab397e98fbc5aa95e12ff4879504'
TECH = ('reservoir','open_pumped','pondage','run_of_river')
# Existing audited HR storage proxies and the PECD SI/BA storage evidence are separate from turbine power.
FLEET = {
    ('HDAM_HR','HR1','HR'): ('reservoir',1358.7,1780.856,None,.9),
    ('HPHS_HR','HR1','HR'): ('open_pumped',275.4,14.8,240.,.9),
    ('HROR_HR','HR1','HR'): ('run_of_river',438.3,0.,None,None),
    ('HPHS_SI','SI1','SI'): ('open_pumped',185.,2.6,180.,None),
    ('HROR_SI','SI1','SI'): ('run_of_river',1128.1,0.,None,None),
    ('HDAM_BA','BA1','BA'): ('reservoir',1619.29,1669.,None,None),
    ('HPHS_BA','BA1','BA'): ('open_pumped',420.,3.4,420.,None),
    ('HROR_BA','BA1','BA'): ('run_of_river',260.,0.,None,None),
}


def near(a,b):
    return isclose(a,b,rel_tol=0,abs_tol=TOL)


def find_native_row(model,var,name,*,coefficient=None):
    col=model.getCol(var)
    found=[]
    for i in range(col.size()):
        r=col.getConstr(i)
        if r.ConstrName.startswith(name+'[') and (coefficient is None or near(model.getCoeff(r,var),coefficient)):
            found.append(r)
    if len(found)!=1:
        raise ValueError(f'Expected one {name} row at {var.VarName}; found {len(found)}')
    return found[0]


def read_hydro(raw, report):
    if (sha256(raw).hexdigest()!=SOURCE_SHA or report['hourly_sha256']!=SOURCE_SHA
            or report['target_year']!=YEAR or report['weather_cases']!=36 or report['rows']!=26280):
        raise ValueError('Unexpected ERAA zonal hydro source')
    reader = csv.DictReader(StringIO(raw.decode('utf8')))
    fields = [k+'_natural_turbine_equivalent_mwh' for k in TECH]
    if reader.fieldnames!=['system','period',*fields]:
        raise ValueError('Unexpected ERAA hydro schema')
    series = {s:{k:[] for k in TECH} for s in ('HR','BA','SI')}
    for row in reader:
        s=row['system']
        if int(row['period'])!=len(series[s][TECH[0]])+1:
            raise ValueError('Missing or duplicate ERAA hydro hour')
        for k,field in zip(TECH,fields):
            value=float(row[field])
            if not isfinite(value) or value<0.:
                raise ValueError('Invalid hourly hydro resource')
            series[s][k].append(value)
    for s,streams in series.items():
        for k,values in streams.items():
            meta=report['zones'][s].get(k)
            target=meta['calendar_mwh'] if meta and meta['active'] else 0.
            if len(values)!=8760 or not near(fsum(values),target):
                raise ValueError('Hourly water does not match the active published input: '+s+'/'+k)
    if any(series['SI']['open_pumped']) or any(series['SI']['reservoir']):
        raise ValueError('SI closed-loop pumped storage may not receive natural inflow')
    return series


def apply_hydro_stock(row):
    key=row['unit_name'],row['Zone'],row['System']
    category,capacity,energy,pump,efficiency=FLEET[key]
    expected={'reservoir':'HDAM','open_pumped':'HPHS','run_of_river':'HROR'}[category]
    if row['technology']!=expected or row['fuel_type']!='Hydro':
        raise ValueError('Expected the native hydro technology: '+repr(key))
    row.update(cap_mw=str(capacity),max_cap=str(capacity),STOCapacityGWh=str(energy))
    if pump is not None:
        row['STOMaxChargingPower']=str(pump)
    if efficiency is not None:
        row['efficiency']=str(efficiency)


def install_hydro_inputs(ctx, series):
    if (tuple(ctx['years'])!=(YEAR,) or tuple(ctx['periods'])!=HOURS
            or ctx['simple_curves'] is not False or not ctx['hydro_storage']):
        raise ValueError('Expected native year-specific, full-hour hydro storage equations')
    ledger={}
    for key,(kind,capacity,energy,pump,_) in FLEET.items():
        u,z,s=key
        if not near(ctx['max_load'][key],capacity):
            raise ValueError('Dated hydro stock did not reach the native reader: '+repr(key))
        group=series[s]
        if kind=='run_of_river':
            # Preserve the two source streams in the receipt; the native turbine is shared.
            water=[a+b for a,b in zip(group['pondage'],group[kind])]
            eta=1.
            field='aval_factor_plant'
        else:
            tag='hdam' if kind=='reservoir' else 'hphs'
            eta=float(ctx[tag+'_efficiency'][u,z])
            if not 0.<eta<=1. or not near(ctx[tag+'_max_storage'][u,z],energy):
                raise ValueError('Unexpected hydro efficiency or storage capacity')
            field=tag+'_inflow'
            water=group[kind]
        for p,total in zip(HOURS,water):
            ctx[field][u,p,YEAR]=total/(capacity*eta**2)
        ledger[key]=dict(category=kind,capacity_mw=capacity,pump_mw=pump,storage_gwh=energy,
                         efficiency=eta,hourly_water_mwh=water,
                         source_annual_mwh={k:fsum(group[k]) for k in
                             (('pondage','run_of_river') if kind=='run_of_river' else (kind,))})
    return ledger


def audit_and_finalize_hydro(ctx,ledger):
    """Check storage balances and replace native RoR must-run by water and turbine ceilings."""
    model=ctx['ucp']
    model.update()
    receipts={}
    def terms(row,sense,rhs,expected):
        if (row.Sense!=sense or not near(row.RHS,rhs) or model.getRow(row).size()!=len(expected)
                or any(not near(model.getCoeff(row,v),c) for v,c in expected)):
            raise ValueError('Unexpected hydro row: '+row.ConstrName)

    for key,d in ledger.items():
        u,z,s=key
        kind,cap,eta=d['category'],d['capacity_mw'],d['efficiency']
        available=ctx['available_capacity_year'][u,z,s,YEAR]
        inv=ctx['cap_inv'][u,z,s,YEAR]
        if not near(available.getConstant(),cap) or available.size()!=1 or available.getVar(0).index!=inv.index:
            raise ValueError('Hydro turbine does not match dated stock: '+u)
        if not near(available.getCoeff(0),1.):
            raise ValueError('Unexpected investment coefficient in the hydro turbine expression')
        spill=[]
        changed=[]
        for p,water in zip(HOURS,d['hourly_water_mwh']):
            idx=u,z,s,p,YEAR
            gen=ctx['generation'][idx]
            if kind=='run_of_river':
                row=find_native_row(model,gen,'max_output_hror')
                coeff=water/cap
                terms(row,'=',water,[(gen,1.)]+([(inv,-coeff)] if coeff else []))
                model.chgCoeff(row,inv,0.)
                row.Sense='<'
                turbine=model.addConstr(gen<=cap,name=f'zonal_hydro_ror_turbine[{u},{p}]')
                changed.append((row,turbine,gen,water))
                spill.append(max(0.,water-cap))
                continue
            pumped=kind=='open_pumped'
            tag='hphs' if pumped else 'hdam'
            current=ctx['storage_level'][u,z,p,YEAR]
            prev=ctx['storage_level0'][u,z,YEAR] if p==1 else ctx['storage_level'][u,z,p-1,YEAR]
            spillvar=ctx['sto_out_flow'][u,z,p,YEAR]
            family=(('hphs_storage_level_cap_0' if p==1 else 'hphs_storage_level_period') if pumped
                    else ('storage_level_cap_0' if p==1 else 'storage_level_balance'))
            constraint=find_native_row(model,current,family,coefficient=1.)
            coefficients=[(current,1.),(prev,-1.),(gen,1./eta),(spillvar,1.)]
            if pumped:
                pump=ctx['hphs_pumpback'][idx]
                coefficients.append((pump,-eta))
                pump_row=find_native_row(model,pump,'max_output_hphs_pump')
                terms(pump_row,'<',cap,[(pump,1.),(inv,-1.)])
                model.chgCoeff(pump_row,inv,0.)
                pump_row.RHS=d['pump_mw']
                changed.append((pump_row,None,pump,d['pump_mw']))
            terms(constraint,'=',water/eta,coefficients)
            turbine=find_native_row(model,gen,'max_output_'+tag+('_gen' if pumped else ''))
            terms(turbine,'<',cap,[(gen,1.),(inv,-1.)])
            upper=find_native_row(model,current,'hphs_storage_level_max' if pumped else 'storage_level_max')
            terms(upper,'<',d['storage_gwh']*1000,[(current,1.)])
        if kind!='run_of_river':
            end=ctx['storage_level'][u,z,HOURS[-1],YEAR]
            terminal=find_native_row(model,end,'hydro_soc_cycle')
            terms(terminal,'=',0.,[(end,1.),(ctx['storage_level0'][u,z,YEAR],-1.)])
        model.update()
        for row,turbine,var,rhs in changed:
            terms(row,'<',rhs,[(var,1.)])
            if turbine is not None:
                terms(turbine,'<',cap,[(var,1.)])
        receipts[u]=dict(system=s,category=kind,capacity_mw=cap,storage_gwh=d['storage_gwh'],
                         pump_mw=d['pump_mw'],source_annual_mwh=d['source_annual_mwh'],
                         natural_water_above_ror_turbine_mwh=fsum(spill),
                         hours_above_ror_turbine=sum(v>0. for v in spill),
                         terminal_cycle=kind!='run_of_river',hours=8760)
    return receipts

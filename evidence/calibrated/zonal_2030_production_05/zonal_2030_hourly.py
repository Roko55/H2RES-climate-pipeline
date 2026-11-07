"""Export actual hourly grid and hydro balances, then re-audit the persisted data."""

import csv
import gzip
from io import TextIOWrapper
from math import fsum, isfinite


ZONES={'HR':'HR1','SI':'SI1','BA':'BA1'}
HOURS=tuple(range(1,8761))
YEAR=2030
TOL=1e-7
GENERATION_FAMILIES=('fossil_units','hphs_units','hdam_units','hror_units','nondisp_units')
FIELDS=('system','period','load_mwh','eens_mwh','ceep_mwh','generation_mwh','solar_mwh','wind_mwh',
        'gross_import_pre_loss_mwh','import_delivered_mwh','gross_export_sending_mwh',
        'battery_charge_grid_mwh','battery_discharge_grid_mwh','battery_overlap_grid_mwh',
        'reservoir_generation_mwh','ror_generation_mwh','pumped_generation_mwh','pumped_charge_grid_mwh',
        'pumped_overlap_grid_mwh','natural_reservoir_mwh_e','natural_ror_mwh_e','natural_pumped_mwh_e',
        'hydro_pump_credit_mwh_e','hydro_spill_mwh_e','ror_unused_mwh_e','hydro_storage_delta_mwh_e')


def grid_residual(r):
    return fsum(r[k] for k in ('generation_mwh','eens_mwh','import_delivered_mwh','battery_discharge_grid_mwh'))-fsum(
        r[k] for k in ('load_mwh','ceep_mwh','gross_export_sending_mwh','battery_charge_grid_mwh','pumped_charge_grid_mwh'))


def water_residual(r):
    return fsum(r[k] for k in ('natural_reservoir_mwh_e','natural_ror_mwh_e','natural_pumped_mwh_e','hydro_pump_credit_mwh_e'))-fsum(
        r[k] for k in ('reservoir_generation_mwh','ror_generation_mwh','pumped_generation_mwh','hydro_spill_mwh_e',
                       'ror_unused_mwh_e','hydro_storage_delta_mwh_e'))


def audit_generation_membership(ctx,stock):
    included={u for family in GENERATION_FAMILIES for u in ctx[family]}
    active=[r for r in stock['rows'] if r['existing_mw']>0.]
    missing=[]
    for r in active:
        u,z,s=r['unit'],r['zone'],r['system']
        if u not in included or any(ctx['ucp'].getCoeff(ctx['energy_balance'][z,s,p,YEAR],
                     ctx['generation'][u,z,s,p,YEAR])!=1. for p in (HOURS[0],HOURS[-1])):
            missing.append(f"{s}/{u} ({r['existing_mw']} MW)")
    if missing:
        raise ValueError('Dated active generators absent from native electric balance: '+', '.join(missing))
    return len(active)


def hourly_rows(ctx,hydro):
    """One-hour MWh, with transfer before loss and delivered energy separately."""
    el=set(ctx['transmission_units_el'])
    if ctx['include_secc_links'] or ctx['model_transport_and_industry']:
        raise ValueError('Hourly export is restricted to the zonal electricity configuration')
    model=ctx['ucp']
    for s,z in ZONES.items():
        active=set(ctx['unit_zone_check'][z])&set(ctx['unit_system_check'][s])
        units=[u for family in GENERATION_FAMILIES for u in ctx[family] if u in active]
        if not units or len(set(units))!=len(units):
            raise ValueError('Unexpected generator accounting in '+s)
        types=dict(ctx['unit_tech'])
        links={key:[(u,a,b) for u,x,y,a,b in ctx[group] if (x,y)==(z,s) and u in el]
               for key,group in (('in','primary_links'),('out','primary_links_export'))}
        exterior=[u for u,x,y in ctx['outside_links'] if (x,y)==(z,s) and u in el]
        dams={u:d for (u,x,y),d in hydro.items() if (x,y)==(z,s)}
        previous={u:float(ctx['storage_level0'][u,z,YEAR].X) for u,d in dams.items() if d['category']!='run_of_river'}
        starting=previous.copy()
        for p in HOURS:
            balance=ctx['energy_balance'][z,s,p,YEAR]
            prod={u:float(ctx['generation'][u,z,s,p,YEAR].X) for u in units}
            pin=[ctx['import_zone_system'][u,YEAR,p,z,s,a,b] for u,a,b in links['in']]
            pin += [ctx['import_outside'][u,YEAR,p,z,s] for u in exterior]
            pout=[ctx['export_zone_system'][u,YEAR,p,z,s,a,b] for u,a,b in links['out']]
            pout += [ctx['export_outside'][u,YEAR,p,z,s] for u in exterior]
            delivery=[model.getCoeff(balance,v) for v in pin]
            if any(not 0.<k<=1.+TOL for k in delivery) or any(model.getCoeff(balance,v)!=-1. for v in pout):
                raise ValueError('Invalid direction or loss in the electrical balance')
            r={k:0. for k in FIELDS[2:]}
            r.update(system=s,period=p,load_mwh=ctx['demand'][p,z,s]*ctx['demand_change_policy_df_distr_year'][s,z,YEAR],
                     eens_mwh=float(ctx['noloadel'][p,YEAR,z,s].X),ceep_mwh=float(ctx['ceep'][p,YEAR,z,s].X),
                     generation_mwh=fsum(prod.values()),
                     solar_mwh=fsum(v for u,v in prod.items() if types[u]=='PHOT'),
                     wind_mwh=fsum(v for u,v in prod.items() if types[u]=='WTON'),
                     gross_import_pre_loss_mwh=fsum(v.X for v in pin),
                     import_delivered_mwh=fsum(v.X*k for v,k in zip(pin,delivery)),
                     gross_export_sending_mwh=fsum(v.X for v in pout))
            for u in ctx['Stat_storage_unit']:
                key=u,p,YEAR,z,s
                charge=float(ctx['stat_charge'][key].X)
                discharge=float(ctx['stat_discharge'][key].X)*ctx['Sta_sto_eff'][u,z,s]
                r['battery_charge_grid_mwh']+=charge
                r['battery_discharge_grid_mwh']+=discharge
                r['battery_overlap_grid_mwh']+=max(0.,min(charge,discharge))
            for u,d in dams.items():
                gen=prod[u]
                water=d['hourly_water_mwh'][p-1]
                kind,eta=d['category'],d['efficiency']
                if kind=='run_of_river':
                    r['ror_generation_mwh']+=gen
                    r['natural_ror_mwh_e']+=water
                    r['ror_unused_mwh_e']+=water-gen
                    continue
                store=float(ctx['storage_level'][u,z,p,YEAR].X)
                r['hydro_spill_mwh_e']+=float(ctx['sto_out_flow'][u,z,p,YEAR].X)*eta
                r['hydro_storage_delta_mwh_e']+=(store-previous[u])*eta
                previous[u]=store
                if kind=='reservoir':
                    r['reservoir_generation_mwh']+=gen
                    r['natural_reservoir_mwh_e']+=water
                else:
                    pump=float(ctx['hphs_pumpback'][u,z,s,p,YEAR].X)
                    r['natural_pumped_mwh_e']+=water
                    r['pumped_generation_mwh']+=gen
                    r['pumped_charge_grid_mwh']+=pump
                    r['pumped_overlap_grid_mwh']+=max(0.,min(gen,pump))
                    r['hydro_pump_credit_mwh_e']+=pump*eta**2
            yield r
        if any(abs(previous[u]-v)>TOL for u,v in starting.items()):
            raise ValueError('Annual hydro storage cycle failed in '+s)


def audit_file(path,hours=HOURS):
    with gzip.open(path,'rt',encoding='utf8',newline='') as stream:
        reader=csv.DictReader(stream)
        if tuple(reader.fieldnames)!=FIELDS:
            raise ValueError('Invalid hourly result schema')
        report={}
        for system in ZONES:
            rows=[]
            for p in hours:
                row=next(reader,None)
                if row is None or (row['system'],int(row['period']))!=(system,p):
                    raise ValueError('Missing, duplicate or out-of-order hourly result')
                r={k:float(row[k]) for k in FIELDS[2:]}
                if any(not isfinite(v) or (k!='hydro_storage_delta_mwh_e' and v< -TOL) for k,v in r.items()):
                    raise ValueError('Nonphysical hourly result')
                rows.append(r)
            annual={k:fsum(r[k] for r in rows) for k in FIELDS[2:]}
            grid=max(abs(grid_residual(r)) for r in rows)
            water=max(abs(water_residual(r)) for r in rows)
            if (max(grid,water)>TOL or abs(water_residual(annual))>len(hours)*TOL
                    or abs(annual['hydro_storage_delta_mwh_e'])>TOL*2):
                raise ValueError(f'Hour/annual energy closure failed: {system}, grid={grid}, hydro={water}')
            report[system]=dict(annual=annual,hours={k:sum(r[k]>TOL for r in rows) for k in (
                'eens_mwh','ceep_mwh','battery_overlap_grid_mwh','pumped_overlap_grid_mwh')},
                max_hourly_grid_residual_mwh=grid,max_hourly_water_residual_mwh_e=water,
                annual_water_residual_mwh_e=water_residual(annual))
        if next(reader,None) is not None:
            raise ValueError('Extra hourly result rows')
    return dict(rows=len(hours)*len(ZONES),zones=report,
                flow_meter='Gross import before model line losses; delivered import after losses; export on the sending side.',
                water_meter='Electrical energy equivalent; pump credit uses native round-trip conversion; spill and unused RoR are separate.')


def save_hourly(path,ctx,hydro):
    with path.open('xb') as raw, gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as data:
        with TextIOWrapper(data,encoding='utf8',newline='') as stream:
            writer=csv.DictWriter(stream,FIELDS,lineterminator='\n')
            writer.writeheader()
            writer.writerows(hourly_rows(ctx,hydro))
    return audit_file(path)

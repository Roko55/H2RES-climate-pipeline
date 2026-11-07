"""Validate, install and audit signed net exchange for the isolated 2020 replay."""
import csv
from math import fsum, isfinite
from statistics import mean

from prepare_native_replay import csv_rows, sha


PROFILE = 'r5_trade_hourly_preflight/candidate_hourly_2020.csv'
MONTHLY = 'historical_monthly_trade_preflight_2020.json'
CAPACITY = 'data/SC/import_export_HR_2020_2050_sdewes.csv'
CAPACITY_SHA = '73330f9621dd768be93097e3a73bbd15d6cf3c945fff8c0706b44635acd5bbb8'
RAMPS = (
    'rump_up   = ucp.addConstrs(imports[period, year] <= imports[period-1, year]*(1 + 0.2)  for period in periods[1:] for year in years)',
    'rump_down = ucp.addConstrs(imports[period, year] >= imports[period-1, year]*(1 - 0.2)  for period in periods[1:] for year in years)',
)


def render_signed(code):
    for line in RAMPS:
        if code.count(line) != 1:
            raise ValueError('Native import-only ramp anchor changed')
        code = code.replace(line, '', 1)
    return code


def require_close(a,b,label,tol=1e-6):
    if not isfinite(a) or not isfinite(b) or abs(a-b)>tol:
        raise ValueError(f'{label}: {a} != {b}')


def read_trade(root, baseline, months):
    rows=csv_rows((root/PROFILE).read_bytes())
    raw=csv_rows(baseline)
    months_by_id={int(r['month']):r for r in months['months']}
    if len(rows)!=8760 or len(raw)!=8760 or set(months_by_id)!=set(range(1,13)):
        raise ValueError('Invalid historical trade chronology')
    result=[]
    for p,r,b in zip(range(1,8761),rows,raw):
        m=int(r['month'])
        if int(r['period'])!=p or int(b['period'])!=p or r['time_utc']!=b['time_utc'] or m!=int(r['time_utc'][5:7]):
            raise ValueError(f'Historical trade clock mismatch at hour {p}')
        physical=float(b['physical_net_observed_mwh'])
        krsko=float(b['krsko_hr_mwh'])
        signed=float(r['signed_other_net_mwh'])
        in_cap,out_cap=float(r['archived_in_cap_mw']),float(r['archived_out_cap_mw'])
        if not all(isfinite(v) for v in (physical,krsko,signed,in_cap,out_cap)) or not (in_cap>=0 and out_cap>=0):
            raise ValueError('Invalid historical exchange values')
        require_close(signed,physical-krsko+months_by_id[m]['physical_shape_offset_mw'],'hourly source identity')
        require_close(float(r['gross_in_from_signed_proxy_mwh']),max(signed,0.),'positive leg')
        require_close(float(r['gross_out_from_signed_proxy_mwh']),max(-signed,0.),'negative leg')
        if not -out_cap-1e-8 <= signed <= in_cap+1e-8:
            raise ValueError(f'Historical exchange outside archived caps: {p}')
        result.append(dict(period=p,time_utc=r['time_utc'],month=m,signed=signed,physical=physical,krsko=krsko,
                           import_cap=in_cap,export_cap=out_cap))
    for m,s in months_by_id.items():
        require_close(fsum(r['signed'] for r in result if r['month']==m),s['ei_hp_consistent_other_net_mwh'],
                      f'conditioned net month {m}')
    require_close(fsum(r['signed'] for r in result),1618900.,'conditioned annual net')
    return result


def install(ctx, trade, capacity_file):
    if ctx['rps_inv'] or ctx['years']!=[2020] or sha(capacity_file.read_bytes())!=CAPACITY_SHA:
        raise ValueError('Signed historical trade requires the pinned 2020 case with RPS inactive')
    caps={int(r['period']):r for r in csv_rows(capacity_file.read_bytes()) if r['year']=='1'}
    model=ctx['ucp']
    for r in trade:
        p=r['period']
        v=ctx['imports'][p,2020]
        require_close(float(caps[p]['Import_capacity']),r['import_cap'],'frozen import capacity')
        require_close(float(ctx['import_ntc'][p]),r['import_cap'],'built import capacity')
        require_close(float(caps[p]['export_capacity']),r['export_cap'],'frozen export capacity')
        v.LB=-r['export_cap']
        v.UB=r['import_cap']
        model.addConstr(v==r['signed'],name=f'historical_signed_exchange_{p}')


def verify_built(ctx,trade):
    model=ctx['ucp']
    coefficients=[]
    for r in trade:
        p=r['period']
        v=ctx['imports'][p,2020]
        require_close(v.LB,-r['export_cap'],'built export bound')
        require_close(v.UB,r['import_cap'],'built import bound')
        require_close(model.getCoeff(ctx['meet_demand'][p,2020],v),1.,'signed balance coefficient')
        coefficients.append(float(v.Obj))
    if max(coefficients)-min(coefficients)>1e-9:
        raise ValueError('Exchange objective coefficient is not constant over the fixed schedule')
    return coefficients[0]


def audit(ctx, groups, loads, ledger, trade, official, out):
    rows=[]
    for r,l,t in zip(loads,ledger,trade):
        p=t['period']
        key=p,2020
        gen=fsum(ctx['generation'][u,p,2020].X for u in groups)
        fc=fsum(ctx['generation_FC'][u,p,2020].X for u in ctx['FC_units'])
        battery_net=fsum(ctx['stat_sto_in'][u,p,2020].X-ctx['stat_sto_out'][u,p,2020].X*ctx['Sta_sto_eff'][u]
                         for u in ctx['Stat_storage_unit'])
        ev_in=ctx['ev_sto_in'][key].X
        ev_out=ctx['ev_sto_out'][key].X*ctx['ev_Grid_eff']
        demand=fsum((r['residual'],r['pump'],l['existing_household_cooling_grid_mwh'],ev_in))
        flow=float(ctx['imports'][key].X)
        deficit=ctx['noload_elec'][key].X
        spill=ctx['ceep'][key].X
        rows.append(dict(period=p,time_utc=t['time_utc'],month=t['month'],signed_other_net_mwh=flow,
            candidate_other_net_mwh=t['signed'],physical_total_net_mwh=t['physical'],
            krsko_mwh=t['krsko'],demand_mwh=demand,generation_including_krsko_mwh=gen,
            unserved_mwh=deficit,curtailed_mwh=spill,battery_net_mwh=battery_net,
            balance_error_mwh=fsum((gen,fc,flow,ev_out,deficit,-demand,-battery_net,-spill))))
    with (out/'exchange_hourly.csv').open('x',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k:round(v,8) if isinstance(v,float) else v for k,v in r.items()} for r in rows)
    monthly=[]
    for ref in official['months']:
        cohort=[r for r in rows if r['month']==ref['month']]
        actual=fsum(r['signed_other_net_mwh'] for r in cohort)
        physical=fsum(r['physical_total_net_mwh']-r['krsko_mwh'] for r in cohort)
        krsko=fsum(r['krsko_mwh'] for r in cohort)
        official_other=ref['eurostat_import_mwh']-ref['eurostat_export_mwh']-krsko
        monthly.append(dict(month=ref['month'],signed_other_net_mwh=actual,physical_other_proxy_mwh=physical,
            original_eurostat_other_net_mwh=official_other,conditioned_other_net_mwh=ref['ei_hp_consistent_other_net_mwh'],
            difference_vs_physical_proxy_mwh=actual-physical,difference_vs_unscaled_official_mwh=actual-official_other,
            difference_vs_conditioned_mwh=actual-ref['ei_hp_consistent_other_net_mwh']))
    error=[r['signed_other_net_mwh']-(r['physical_total_net_mwh']-r['krsko_mwh']) for r in rows]
    flow=[r['signed_other_net_mwh'] for r in rows]
    return dict(annual_other_net_mwh=fsum(flow),positive_leg_mwh=fsum(max(x,0.) for x in flow),
        negative_leg_mwh=fsum(max(-x,0.) for x in flow),peak_import_mw=max(flow),peak_export_mw=-min(flow),
        import_hours=sum(x>1e-7 for x in flow),export_hours=sum(x< -1e-7 for x in flow),
        observed_physical_proxy_mae_mw=mean(abs(x) for x in error),
        observed_physical_proxy_max_abs_difference_mw=max(map(abs,error)), monthly=monthly,
        max_schedule_error_mw=max(abs(r['signed_other_net_mwh']-r['candidate_other_net_mwh']) for r in rows),
        max_balance_error_mwh=max(abs(r['balance_error_mwh']) for r in rows),
        unserved_mwh=fsum(r['unserved_mwh'] for r in rows),curtailed_mwh=fsum(r['curtailed_mwh'] for r in rows),
        deficit_hours=sum(r['unserved_mwh']>1e-6 for r in rows),
        curtailed_hours=sum(r['curtailed_mwh']>1e-6 for r in rows),
        peak_deficit= max(rows,key=lambda r:r['unserved_mwh']),
        archived_directional_caps_not_verified_operating_ntc=True)

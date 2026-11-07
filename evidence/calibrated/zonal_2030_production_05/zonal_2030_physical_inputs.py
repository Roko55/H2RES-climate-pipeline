"""Read and qualify dated stock and load boundaries for the zonal experiment."""
from collections import defaultdict
import csv
from hashlib import sha256
import io
import json
from math import fsum, isclose, isfinite
from pathlib import Path
from zipfile import ZipFile

from eraa_2030_built_exchange import HOURS


ROOT = Path(__file__).resolve().parent
ZONES = {'HR':'HR1','SI':'SI1','BA':'BA1'}
ANCHOR = 2030
STORAGE = 'Liion_storage'
TOL = 1e-7
ERAA_RAW = 'inputs/entsoe_eraa_2025/ERAA_2025_Dashboard_RawData.zip'
ERAA_SHA = '45e16e952b2548fd3a8f337f30c3662247ed921ad2d36d250c5bc3098865f636'
ERAA_URL = 'https://www.entsoe.eu/eraa/2025/modelling-data/'
D13_SHA = '91cb41db4ab0ed8aea490f3dfa26fdaa9fd83b7d3893426bdbd346a4b99d9b03'
HERA_SHA = '713a4be626a0af9a6c097681b904f797c8e6cf119bb5e6a60015f505997548de'
DEMAND_SHA = '51a4ebc4b46b123d704f7e4f5402ab708ba63454ce53dd81ddb421b2ed10be05'


def read_json(path):
    return json.loads(path.read_bytes())


def read_pinned(path,expected):
    raw=path.read_bytes()
    if sha256(raw).hexdigest()!=expected:
        raise ValueError('Input bytes changed: '+str(path))
    return raw


def csv_rows(raw):
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))


def era_comparison(root):
    with ZipFile(io.BytesIO(read_pinned(root/ERAA_RAW,ERAA_SHA))) as z:
        paths={Path(name).name:name for name in z.namelist()}
        demand=csv_rows(z.read(paths['Aggregated_Demand.csv']))
        capacity=csv_rows(z.read(paths['GenerationCapacities.csv']))
    projected_load, projected_stock = {}, {}
    for year in (2028,2030):
        projected_load[year]={}
        projected_stock[year]={}
        for country in ZONES:
            market=country+'00'
            load,=[r for r in demand if (r['data version'],r['Target_Year'],r['Market_Node'],
                r['Measure'],r['Measure_Type'])==('ERAA 2025 final',str(year),market,'Annual Demand (TWh)','AVG')]
            projected_load[year][country]=float(load['Value'])*1e6
            stock=defaultdict(float)
            for r in capacity:
                if (r['data_version'],r['Target year'],r['Market_Node'],r['Operational_Status'])==(
                        'ERAA 2025 final',str(year),market,'Available on market'):
                    stock[r['Technology']]+=float(r['Value'])
            if not stock:
                raise ValueError('No projected ERAA capacities for '+market)
            projected_stock[year][country]=dict(stock)
    return dict(source=ERAA_URL,source_sha256=ERAA_SHA,mean_annual_load_mwh=projected_load,
                available_technology_mw=projected_stock,
                scope='Projection comparator only. Do not substitute for 2025 physical inventory or assume meter equivalence.')


def active_d13_stock(root):
    manifest=read_json(root/'zonal_2020_meter_d13_staged_inputs.json')
    status=read_json(root/'zonal_2020_meter_d13_status.json')
    if (status.get('state')!='conditional_dispatch_verified' or status.get('solver_status')!=2
            or status.get('planning_approved') is not False or manifest.get('inherited_results') is not False
            or manifest['active_csv_and_workbook_sha256']['GENCO']!=D13_SHA):
        raise ValueError('The starting inventory is not the verified conditional 2020 case')
    rows=csv_rows(read_pinned(root/'zonal_2020_meter_d13_inventory.csv',D13_SHA))
    result={}
    for r in rows:
        system=r['System']
        if system in ZONES and ZONES[system]==r['Zone']:
            key=r['unit_name'],r['Zone'],system
            rating=float(r['cap_mw'])
            if key in result or not isfinite(rating) or rating<0.:
                raise ValueError('Duplicate or invalid active D13 inventory key')
            result[key]=dict(mw=rating,technology=r['technology'],fuel=r['fuel_type'])
    if result['NE_Krsko','SI1','SI']['mw']!=696. or any(
            k[-1]=='HR' and v['fuel']=='Nuclear' for k,v in result.items()):
        raise ValueError('Krskos physical stock is not exclusively in Slovenia')
    return result


def dated_batteries(dated,hr_policy):
    """Keep grid MW and internal MWh separate from all historical investment ledgers."""
    si=dated['slovenia']
    assets=si['verified_battery_assets']
    def observed_si(year):
        return [fsum(a[k] for a in assets if a['first_year']<=year) for k in ('mw','mwh')]
    for year in (2020,2025):
        if observed_si(year)!=si[f'verified_grid_battery_mw_mwh_{year}']:
            raise ValueError('SI named asset dates no longer match their total')
    def hr(year):
        if year==2020:
            return [0.,0.]
        r=hr_policy['units'][STORAGE]['existing_by_year'][str(year)]
        return [r['grid_power_mw'],r['energy_mwh']]
    return {y:{'HR':hr(y),'SI':observed_si(min(2025,y)),'BA':[0.,0.]} for y in (2020,2025,2030)}


def archive_capacity_differences(root):
    ctrl=root/'electricity_2030_batch_02'
    receipt=read_json(ctrl/'control_result.json')
    if receipt.get('eligible_for_fixed_portfolio_tests') is not False:
        raise ValueError('Unexpected comparison portfolio classification')
    result=json.loads(read_pinned(ctrl/'portfolio.json',receipt['portfolio_sha256']))
    data={}
    for key,value in result['generationcapyear'].items():
        unit,zone,system,year=json.loads(key)
        if year!=ANCHOR or ZONES.get(system)!=zone or unit in data:
            raise ValueError('Ambiguous active old generator key')
        data[unit]=dict(system=system,optimized_2030_mw=value)
    unbuilt=('New_TE_Tuzla_B','New_TE_Bugojno','New_TE_Kongora','New_TE_Kamengrad')
    if any(data[u]['system']!='BA' for u in unbuilt):
        raise ValueError('Archived planned units have unexpected geographic scope')
    return dict(active_generator_keys=len(data),source_portfolio_sha256=receipt['portfolio_sha256'],
                ba_unbuilt_archive_mw={u:data[u]['optimized_2030_mw'] for u in unbuilt},
                hr_archived_hydro_mw={u:data[u]['optimized_2030_mw'] for u in ('HDAM_HR','HPHS_HR','HROR_HR')},
                si_archived_mw={u:data[u]['optimized_2030_mw'] for u in
                                ('TE_Sostanj','TPP_Brestanica','SI_PV','SI_Wind','NE_Krsko')})


def generation_crosswalk(root,dated,old_stock):
    """Map only independently identified physical stock; expose unresolved native rows."""
    solar=read_json(root/'solar_stock_2025_preflight/preflight.json')
    wind=read_json(root/'wind_stock_zone_preflight/preflight.json')
    gas=read_json(root/'gas_stock_2025_preflight/preflight.json')
    gas_rows=csv_rows(read_pinned(root/'gas_stock_2025_preflight/candidate_generator_rows.csv',gas['output_sha256']))
    rates={r['unit_name']:float(r['cap_mw']) for r in gas_rows}
    if not isclose(fsum(rates.values()),gas['available_gas_candidate_mw'],rel_tol=0.,abs_tol=TOL):
        raise ValueError('Dated Croatian gas rating does not close')
    hr={r['unit_name']:r['candidate_connection_mw'] for r in solar['candidate_by_zone'].values()}
    hr.update({'HR_Wind_'+k:v['installed_mw'] for k,v in wind['stock_by_zone']['2025']['regular'].items()})
    hr.update(HDAM_HR=dated['croatia_bosnia_crosswalk']['zonal_hr_reservoir_candidate_mw'],
              HPHS_HR=old_stock['HPHS_HR','HR1','HR']['mw'],
              HROR_HR=old_stock['HROR_HR','HR1','HR']['mw'],TE_Plomin_2=199.,
              KTE_Jertovec=0.,TE_Rijeka=0.,TE_Sisak=0.)
    si=dated['slovenia']['installed_mw_2025']
    thermal=dated['slovenia']['dispatchable_thermal_crosschecks']
    ba=dated['bosnia_herzegovina']
    ba_stock=ba['installed_mw_2025']
    candidates={
        'HR':dict(mapped_or_named_mw=hr,new_gas_roster_mw=rates,
                  previous_keys_replaced=['TE-TO_Zagreb','EL-TO_Zagreb','TE_-TO_Osijek'],
                  gas_expansion_separate=gas['expansion']),
        'SI':dict(mapped_or_named_mw={'NE_Krsko':si['nuclear_model_physical'],'SI_PV':si['solar'],
                                    'SI_Wind':si['wind'],'HPHS_SI':si['pumped_hydro_generation'],
                                    'Biomas_SI':si['wood_biomass'],'Biogas_SI':si['biogas'],
                                    'Waste_SI':si['municipal_waste']},
                  thermal_scope=thermal,hydro_nonpumped_mw=si['other_hydro']),
        'BA':dict(mapped_or_named_mw={'BA_Wind':ba_stock['wind'],'BA_PV':ba_stock['solar'],
                                    'HPHS_BA':ba_stock['pumped_hydro'],
                                    'TE_Kakanj':ba['available_mw_2025_by_plant']['Kakanj'],
                                    'TE_Ugljevik':ba['available_mw_2025_by_plant']['Ugljevik'],
                                    'TE_Gacko':ba['available_mw_2025_by_plant']['Gacko']},
                  separate_physical_coal_mw={k:ba['available_mw_2025_by_plant'][k] for k in ('Tuzla','Stanari')},
                  retire_archived_planned_only=['New_TE_Tuzla_B','New_TE_Bugojno','New_TE_Kongora','New_TE_Kamengrad'],
                  nonpumped_hydro_mw=ba_stock['hydro']-ba_stock['pumped_hydro'],
                  unallocated_industrial_mw=ba_stock['industrial_power']),
    }
    return candidates


def annual_meter_inputs(root,dated):
    """Document non-interchangeable official quantities before choosing a 2030 meter."""
    read_pinned(root/'inputs/hera_annual_2025.pdf',HERA_SHA)
    ba=dated['bosnia_herzegovina']['annual_2025_gwh']
    si=dated['slovenia']['annual_2025_gwh']
    if not isclose(ba['gross_balance_demand']-ba['plant_use_and_pumping'],
                   ba['net_of_plant_use_and_pumping'],rel_tol=0.,abs_tol=1e-8):
        raise ValueError('BA electricity meter reference does not reconcile')
    return {
        'HR':dict(measured_gwh=19103.,separately_reported_pumping_gwh=224.,
                  grid_reference_excluding_pumping_gwh=18879.,source_sha256=HERA_SHA,pdf_page=50),
        'SI':dict(grid_reference_excluding_pumping_gwh=si['grid_end_user_demand_including_losses_excluding_pumping'],
                  separately_reported_pumping_gwh=si['avce_pumping'],
                  pv_gross_less_grid_injection_gwh=si['smaller_producer_solar_including_own_use']-
                      si['distribution_solar_grid_injections'],
                  notes='Match PV gross with grossed-up load, or grid-only PV with the grid load.'),
        'BA':dict(measured_gwh=ba['gross_balance_demand'],
                  combined_plant_use_and_pumping_gwh=ba['plant_use_and_pumping'],
                  notes='Combined plant use and pumping is not an exact standalone pumping series.'),
    }


def reconcile_hourly_meter(gross, *, modeled_pump, modeled_gross_pv, embedded_pump=None, pv_own_use=None):
    """Put the exogenous load on the same side of the meter as native power."""
    if len(gross)!=len(HOURS):
        raise ValueError('Expected 8760 meter hours')
    if modeled_pump!=(embedded_pump is not None) or modeled_gross_pv!=(pv_own_use is not None):
        raise ValueError('Explicit source series required for each meter correction')
    for data in (gross,embedded_pump,pv_own_use):
        if data is not None and (len(data)!=len(HOURS) or any(not isfinite(v) or v<0. for v in data)):
            raise ValueError('Invalid or incomplete hourly meter series')
    pump=embedded_pump if modeled_pump else (0.,)*len(HOURS)
    pv=pv_own_use if modeled_gross_pv else (0.,)*len(HOURS)
    residual=[fsum((g,-p,s)) for g,p,s in zip(gross,pump,pv)]
    if any(v<0. for v in residual):
        raise ValueError('Meter adjustment exceeds measured demand in at least one hour')
    expected=fsum(gross)-fsum(pump)+fsum(pv)
    if not isclose(fsum(residual),expected,rel_tol=0.,abs_tol=1e-6):
        raise ValueError('Meter adjustment does not close')
    return residual


def annual_shape(values,target):
    if (len(values)!=len(HOURS) or any(not isfinite(v) or v<0. for v in values)
            or not isfinite(target) or target<0.):
        raise ValueError('Invalid annual load or shape')
    total=fsum(values)
    if total<=0.:
        raise ValueError('No positive shape weights')
    return [v*(target/total) for v in values]


def candidate_meter(root,comparators,projection):
    """Prepare an explicit, common 2030 electricity-demand sensitivity."""
    names=('data/planning_demand/preflight.json','data/planning_demand/hourly_by_temperature_member.csv.gz')
    source=root/'bounded_future_demand_preflight'
    physical=root/'future_heat_meter_split_preflight'
    bounded=read_json(source/'preflight.json')
    split=read_json(physical/'preflight.json')
    if sha256((source/'hourly_by_temperature_member.csv.gz').read_bytes()).hexdigest()!=bounded['hourly_sha256']:
        raise ValueError('Existing bounded electricity input changed')
    # Match the already qualified generic Power series; do not subtract EV, heat or cooling.
    import gzip
    selected=bounded['checks_by_year']['2030']['average']['temperature_member']
    with gzip.open(source/'hourly_by_temperature_member.csv.gz','rt',encoding='utf8',newline='') as f:
        data=[r for r in csv.DictReader(f) if int(r['year'])==ANCHOR and int(r['temperature_member'])==selected]
    offsets=csv_rows(read_pinned(physical/'shared_embedded_heat_pump_mwh.csv',
                                 split['outputs_sha256']['shared_embedded_heat_pump_mwh.csv']))
    offsets=[r for r in offsets if int(r['year'])==ANCHOR]
    if tuple(int(r['period']) for r in data)!=HOURS or tuple(int(r['period']) for r in offsets)!=HOURS:
        raise ValueError('Croatian power and pumping clocks differ')
    full=[float(r['generic_power']) for r in data]
    pumping=[float(r['embedded_pumping_mwh']) for r in offsets]
    anchor=next(a for a in split['anchors'] if a['year']==ANCHOR)
    if not (isclose(fsum(full),anchor['legacy_power_mwh'],rel_tol=0.,abs_tol=1e-6)
            and isclose(fsum(pumping),anchor['embedded_pump_mwh'],rel_tol=0.,abs_tol=1e-6)):
        raise ValueError('Croatian meter fails the independent annual closure')
    curves={'HR':reconcile_hourly_meter(full,modeled_pump=True,modeled_gross_pv=False,embedded_pump=pumping)}
    meta=dict(status='candidate_not_native_validated',
              interpretation={
                  'HR':'Full native 2030 electricity Power less Velebit reference pumping; embedded electrified uses retained.',
                  'SI_BA':'ERAA gross demand scenario excludes storage pumping and is compatible with modeled gross PV. '
                          'Use the common published-planning-profile mean in every renewable weather case.',
              },
              hr_original_full_mwh=fsum(full),hr_removed_pump_mwh=fsum(pumping),
              source_hashes={names[1]:bounded['hourly_sha256'],
                             'embedded_pump_csv':split['outputs_sha256']['shared_embedded_heat_pump_mwh.csv']},
              annual_mwh={s:fsum(v) for s,v in curves.items()},
              neighbor_targets_mwh={s:projection['mean_annual_load_mwh'][2030][s] for s in ('SI','BA')},
              neighbor_source_comparators={s:comparators[s] for s in ('SI','BA')})
    return curves,meta


def read_verified_meter(root):
    folder=root/'zonal_2030_demand_preflight'
    meta=read_json(folder/'preflight.json')
    if (meta.get('status')!='verified_input_candidate' or meta.get('year')!=ANCHOR
            or meta.get('hours')!=len(HOURS) or meta.get('native_demand_multiplier')!=1.
            or meta.get('candidate_sha256')!=DEMAND_SHA):
        raise ValueError('Unexpected common zonal demand contract')
    raw=read_pinned(folder/'demand_hourly_2030.csv',DEMAND_SHA)
    rows=csv_rows(raw)
    by_zone={s:[] for s in ZONES}
    for row in rows:
        s=row['System']
        if s not in ZONES or row['Zone']!=ZONES[s]:
            raise ValueError('Demand source contains a nonmodel zone')
        by_zone[s].append((int(row['period']),float(row['Power'])))
    curves={}
    for s,records in by_zone.items():
        if tuple(p for p,_ in records)!=HOURS or any(not isfinite(v) or v<=0. for _,v in records):
            raise ValueError('Missing, repeated or invalid zonal demand hour: '+s)
        curves[s]=[v for _,v in records]
        if not isclose(fsum(curves[s]),meta['annual_mwh'][s],rel_tol=0.,abs_tol=1e-5):
            raise ValueError('Zonal demand does not close annual MWh: '+s)
    return curves,meta


def install_demand_meter(ctx,hourly):
    if tuple(ctx['years'])!=(ANCHOR,) or tuple(ctx['periods'])!=HOURS or set(hourly)!=set(ZONES):
        raise ValueError('Invalid native 2030 demand scope')
    for s,z in ZONES.items():
        values=hourly[s]
        if len(values)!=len(HOURS) or any(not isfinite(v) or v<0. for v in values):
            raise ValueError('Invalid zonal meter inputs')
        ctx['demand_change_policy_df_distr_year'][s,z,ANCHOR]=1.
        for p,v in zip(HOURS,values):
            key=p,z,s
            if key not in ctx['demand']:
                raise ValueError('Expected native zonal demand key')
            ctx['demand'][key]=v


def remove_mandatory_battery_build(ctx):
    """Remove only the verified unconditional 1 MWh row, not any upper limit."""
    if tuple(ctx['years'])!=(ANCHOR,) or tuple(ctx['previous_years_list']):
        raise ValueError('Battery minimum removal is for the fresh 2030 inventory only')
    model=ctx['ucp']
    model.update()
    target={v.index for v in ctx['stat_sto_cap_inv'].values()}
    found={}
    for row in model.getConstrs():
        if not row.ConstrName.startswith('stat_storage_inv_min_period['):
            continue
        terms=model.getRow(row)
        if row.Sense!='>' or row.RHS!=1. or terms.size()!=1 or terms.getCoeff(0)!=1.:
            raise ValueError('Native battery minimum differs from the known artifact')
        index=terms.getVar(0).index
        if index not in target or index in found:
            raise ValueError('Unexpected or duplicate native battery minimum column')
        found[index]=row
    if not target or found.keys()!=target:
        raise ValueError('Missing native battery minimum row')
    removed=[r.ConstrName for r in found.values()]
    model.remove(list(found.values()))
    model.update()
    if any(model.getConstrByName(n) is not None for n in removed):
        raise ValueError('Native battery minimum survived removal')
    return removed


def configure_storage_stock(ctx, stock):
    if set(stock)!=set(ZONES) or tuple(ctx['previous_years_list']) or tuple(ctx['years'])!=(ANCHOR,):
        raise ValueError('Storage needs a fresh physical stock for the three zones')
    expected={(STORAGE,z,s) for s,z in ZONES.items()}
    if not expected<=ctx['Sta_sto_init_cap'].keys():
        raise ValueError('Missing native stationary storage keys')
    for unit,zone,system in expected:
        power,energy=stock[system]
        if (not all(isfinite(v) and v>=0. for v in (power,energy)) or bool(power)!=bool(energy)):
            raise ValueError('Invalid grid power or energy inventory')
        ctx['Sta_sto_init_cap'][unit,zone,system]=energy
        ctx['StaStoDecomInd_policy_df_distr_year'][system,zone,ANCHOR]=1.
        ceiling=ctx['Sta_sto_max_cap'][unit,zone,system]
        if ceiling<energy:
            # Keep the previous allowance for new stock; do not make existing assets infeasible.
            ctx['Sta_sto_max_cap'][unit,zone,system]=energy+ceiling


def install_grid_battery_limits(ctx, stock, *, duration_h=2.):
    """Use observed grid MW for existing batteries and an explicit new-build duration."""
    if (tuple(ctx['periods'])!=HOURS or tuple(ctx['years'])!=(ANCHOR,) or tuple(ctx['previous_years_list'])
            or set(stock)!=set(ZONES) or not isfinite(duration_h) or duration_h<=0.):
        raise ValueError('Expected a clean full-year zonal battery build')
    model=ctx['ucp']
    model.update()
    removed=remove_mandatory_battery_build(ctx)
    obsolete, installed = [], []
    for s,z in ZONES.items():
        power,energy=stock[s]
        key=STORAGE,ANCHOR,z,s
        cap=ctx['stat_sto_cap'][key]
        eta=ctx['Sta_sto_eff'][STORAGE,z,s]
        native_ratio=ctx['stat_stor_ratio'][z,s]
        if not 0.<eta<=1. or not isfinite(native_ratio) or native_ratio<=0.:
            raise ValueError('Unverified native battery efficiency or power ratio')
        rate=eta/duration_h
        rhs=power-rate*energy
        for p in HOURS:
            index=STORAGE,p,ANCHOR,z,s
            charge,discharge=(ctx[name][index] for name in ('stat_charge','stat_discharge'))
            for variable,prefix in ((charge,'stat_storage_inputmax['),(discharge,'stat_storage_outputmax[')):
                col=model.getCol(variable)
                matches=[col.getConstr(i) for i in range(col.size()) if col.getConstr(i).ConstrName.startswith(prefix)]
                if len(matches)!=1:
                    raise ValueError('The native battery inverter constraint is not unique')
                row,=matches
                if (row.Sense!='<' or row.RHS!=0. or model.getRow(row).size()!=2 or
                        model.getCoeff(row,variable)!=1. or model.getCoeff(row,cap)!=-native_ratio):
                    raise ValueError('Native battery power row has unexpected terms')
                obsolete.append(row)
            balance=ctx['energy_balance'][z,s,p,ANCHOR]
            if model.getCoeff(balance,charge)!=-1. or model.getCoeff(balance,discharge)!=eta:
                raise ValueError('Battery power basis is not the grid meter')
            installed.append((charge,discharge,cap,eta,rate,rhs,s,p))
    model.remove(obsolete)
    rows=[model.addConstr(charge+eta*discharge-rate*cap<=rhs,name=f'physical_battery_grid[{s},{p}]')
          for charge,discharge,cap,eta,rate,rhs,s,p in installed]
    model.update()
    for row,(charge,discharge,cap,eta,rate,rhs,s,p) in zip(rows,installed):
        if (row.Sense!='<' or model.getRow(row).size()!=3 or abs(row.RHS-rhs)>TOL or
                any(abs(model.getCoeff(row,v)-k)>TOL for v,k in ((charge,1.),(discharge,eta),(cap,-rate)))):
            raise ValueError(f'Installed battery grid row differs at {s}/{p}')
    return dict(removed_mandatory_minimum=removed,power_rows=len(rows),new_grid_duration_hours=duration_h)


def check_demand_meter(ctx,hourly,annual_targets):
    """Audit actual zonal demand rows against exogenous electricity excluding native pumping."""
    if tuple(ctx['years'])!=(ANCHOR,) or tuple(ctx['periods'])!=HOURS:
        raise ValueError('Expected the full-hour 2030 meter')
    if any(ctx[name] for name in ('model_heating','model_cooling','model_transport_and_industry','model_efuel')):
        raise ValueError('A separate electrified end-use module is active')
    model=ctx['ucp']
    model.update()
    observed={}
    pumps={s:[] for s in ZONES}
    for (unit,z,s,period,year),var in ctx['hphs_pumpback'].items():
        if s in pumps and z==ZONES[s] and year==ANCHOR and period==HOURS[0]:
            pumps[s].append(unit)
    for s,z in ZONES.items():
        values=[]
        for p in HOURS:
            target=hourly[s,p]
            v=ctx['demand_el_zone_system'][p,ANCHOR,z,s]
            balance=ctx['energy_balance'][z,s,p,ANCHOR]
            row=ctx['total_dem_zone_system'][z,s,p,ANCHOR]
            coeff=model.getCoeff(row,v)
            pump_vars=[ctx['hphs_pumpback'][unit,z,s,p,ANCHOR] for unit in pumps[s]]
            excess=ctx['ceep'][p,ANCHOR,z,s]
            if (row.Sense!='=' or abs(coeff)!=1. or model.getRow(row).size()!=2+len(pump_vars)
                    or model.getCoeff(row,excess)!=-coeff
                    or any(model.getCoeff(row,pump)!=-coeff for pump in pump_vars)
                    or model.getCoeff(balance,v)!=-1.
                    or not isclose(row.RHS/coeff,target,rel_tol=0.,abs_tol=TOL)):
                raise ValueError(f'Demand meter differs at {s}/{p}')
            values.append(row.RHS/coeff)
        observed[s]=fsum(values)
        if not isclose(observed[s],annual_targets[s],rel_tol=0.,abs_tol=1e-5):
            raise ValueError('Annual exogenous demand target did not close: '+s)
    return observed


def report(root):
    dated=read_json(root/'zonal_dated_stock_sources.json')
    for k in ('bosnia_herzegovina','slovenia'):
        read_pinned(root/dated[k]['source_file'],dated[k]['source_sha256'])
    croatia=dated['croatia_bosnia_crosswalk']
    read_pinned(root/croatia['hops_source_file'],croatia['hops_source_sha256'])
    stock=active_d13_stock(root)
    batteries=dated_batteries(dated,read_json(root/'future_stationary_storage_policy.json'))
    ba=dated['bosnia_herzegovina']
    coal=fsum(ba['available_mw_2025_by_plant'].values())
    if not isclose(coal,1786.,rel_tol=0.,abs_tol=TOL):
        raise ValueError('BA published available units do not close')
    old_hr=stock['HDAM_HR','HR1','HR']['mw']
    corrected=old_hr-croatia['hep_combined_connection_mw']+croatia['dubrovnik_croatian_available_mw']
    if not isclose(corrected,croatia['zonal_hr_reservoir_candidate_mw'],rel_tol=0.,abs_tol=TOL):
        raise ValueError('Shared hydro allocation does not close')
    met=annual_meter_inputs(root,dated)
    era=era_comparison(root)
    hr,proposed=candidate_meter(root,met,era)
    actual,contract=read_verified_meter(root)
    if (any(not isclose(a,b,rel_tol=0.,abs_tol=TOL) for a,b in zip(hr['HR'],actual['HR']))
            or any(not isclose(contract['annual_mwh'][s],proposed['neighbor_targets_mwh'][s],
                               rel_tol=0.,abs_tol=.05) for s in ('SI','BA'))):
        raise ValueError('The common hourly demand does not match the independent boundaries')
    proposed.update(annual_mwh=contract['annual_mwh'],hourly_sha256=contract['candidate_sha256'],
                    source_status='verified_hourly_input_native_rhs_audit_pending')
    return dict(status='read_only_input_qualification',planning_approved=False,
                source_2020_sha256=D13_SHA,source_2020_scope='conditional physical replay, not an investment handoff',
                existing_battery_grid_mw_and_energy_mwh=batteries,
                ba_2025_available_coal_mw=coal,hr_reservoir_shared_rating_adjustment_mw=[old_hr,corrected],
                official_2025_meter_comparators=met,proposed_2030_meter=proposed,
                archive_active_rows=archive_capacity_differences(root),
                dated_generation_candidate=generation_crosswalk(root,dated,stock),
                era_comparators=era,
                blocking=[
                    'Match dated national figures to native active generator rows and exclude unbuilt BA options.',
                    'Reconcile Croatia/Bosnia water-energy allocations independently of generator MW.',
                    'Validate one exogenous demand meter per zone, with behind-the-meter PV and pumping counted once.',
                    'Verify dated MW and MWh stock, grid power rows and both directions of exchange in the actual native matrix.',
                ])


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='zonal_2030_physical_preflight.json')
    name=parser.parse_args().output
    if Path(name).name!=name or not name.endswith('.json'):
        raise ValueError('Use a new JSON filename inside the current project evidence folder')
    out=ROOT/name
    result=report(ROOT)
    with out.open('x',encoding='utf8') as f:
        json.dump(result,f,indent=2,allow_nan=False)

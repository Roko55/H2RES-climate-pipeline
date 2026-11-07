"""Stage independently supported dated capacities; do not silently fill unresolved rows."""

import csv
from hashlib import sha256
from io import StringIO
import json
from math import fsum, isclose, isfinite
from pathlib import Path

from electricity_krsko_preflight import SOURCE, SOURCE_SHA, electricity_candidate
from zonal_2030_hydro import FLEET, apply_hydro_stock
from zonal_2030_physical_inputs import ZONES, active_d13_stock, csv_rows, generation_crosswalk, read_json, read_pinned


YEAR = 2030
TOL = 1e-7
NO_STOCK = {
    'BA': ('New_TE_Tuzla_B','New_TE_Bugojno','New_TE_Kongora','New_TE_Kamengrad'),
    'SI': ('TETO_Vinca',),
    'HR': ('HR_New_Biomass',),
}
WEATHER_BUILD = ('HR_Wind_','HR_Solar_')
PENDING = {
    'HR': ('Document existing-gas name mapping, CAPEX/fuel keys and new CCGT policy.',
           'Replace legacy biomass with the separately verified biomass/biogas stock and energy ceilings.'),
    'SI': ('Resolve the power-only Ljubljana gas/coal split rather than relabel the old 118 MW coal row.',),
    'BA': ('Install existing Tuzla and Stanari as distinct plants with explicit technical parameter lineage.',
           'Do not automatically treat the industrial and waste candidates as firm grid generation.'),
}
THERMAL_2030 = {
    'BA': {
        'TE_Tuzla_G4': ('TE_Kakanj',180.,3600/12159,'Coal','STUR'),
        'TE_Tuzla_G6': ('TE_Kakanj',200.,3600/10703,'Coal','STUR'),
        'TE_Kakanj_G6': ('TE_Kakanj',100.,3600/11350,'Coal','STUR'),
        'TE_Kakanj_G7': ('TE_Kakanj',208.,3600/11850,'Coal','STUR'),
        'TE_Gacko': ('TE_Gacko',276.,3600/11520,'Coal','STUR'),
        'TE_Ugljevik': ('TE_Ugljevik',279.,3600/11470,'Coal','STUR'),
        'TE_Stanari': ('TE_Kakanj',275.,.34,'Coal','STUR'),
    },
    'SI': {'TE_PPE_TOL': ('TPP_Brestanica',132.,.50,'Gas','COMC')},
}
THERMAL_REPLACED = {'BA':('TE_Kakanj',), 'SI':('TE_TO_Ljubljana',)}
THERMAL_REFERENCES = {
    'ba_active_2030': 'https://www.nosbih.ba/files/2026/06/20260604-lat-Indikativni-plan-razvoja-proizvodnje-2027-2036.pdf',
    'stanari_design': 'https://www.eft-power.net/stiri/eft-and-dongfang-electric-corporation-in-epc-deal-for-stanari-tpp-9wzer',
    'ljubljana_operation': 'https://www.nas-stik.si/novice/podrobnosti-novice/187energetsko-dalec-najucinkovitejsa-termoelektrarna-v-sloveniji171',
    'ljubljana_coal_exit': 'https://www.ljubljana.si/sl/aktualno/novice/nova-cena-toplote-v-ogrevalnem-letu-20252026',
}


def near(a,b):
    return isclose(float(a),float(b),rel_tol=0,abs_tol=TOL)


def supported_stock(root):
    dated = read_json(root/'zonal_dated_stock_sources.json')
    cross = generation_crosswalk(root,dated,active_d13_stock(root))
    candidates = {}
    for system,z in ZONES.items():
        values = dict(cross[system]['mapped_or_named_mw'])
        for unit in tuple(values):
            if unit.startswith(('HDAM_','HPHS_','HROR_')):
                values.pop(unit)
        if system=='SI':
            thermal = dated['slovenia']['dispatchable_thermal_crosschecks']
            values.update(TE_Sostanj=thermal['sostanj_6_net_mw'],TPP_Brestanica=thermal['brestanica_installed_mw'])
        values.update(dict.fromkeys(NO_STOCK[system],0.))
        candidates.update({(u,z,system):v for u,v in values.items()})
    candidates.update({key:row[1] for key,row in FLEET.items()})
    return candidates,cross


def install_thermal_roster(rows, stock):
    base={(row['unit_name'],row['Zone'],row['System']):row for row in rows}
    aliases={}
    for s,roster in THERMAL_2030.items():
        z=ZONES[s]
        for name in THERMAL_REPLACED[s]:
            stock[name,z,s]=0.
        for name,(origin,mw,eta,fuel,tech) in roster.items():
            original=base[origin,z,s]
            if original['fuel_type']!=fuel or original['technology']!=tech:
                raise ValueError('Wrong native thermal archetype for '+name)
            key=name,z,s
            rate=(.202 if fuel=='Gas' else float(original['CO2Intensity'])*float(original['efficiency']))
            params=dict(unit_name=name,cap_mw=str(mw),max_cap=str(mw),efficiency=str(eta),
                        CO2Intensity=str(rate/eta),CHPType='N')
            if key in base:
                if name!=origin:
                    raise ValueError('A thermal alias already exists: '+name)
                base[key].update(params)
            else:
                if any(x[0]==name for x in base):
                    raise ValueError('Thermal unit name would collide with another system')
                rows.append(dict(original,**params))
                aliases[name]=origin
            stock[key]=mw
    if fsum(entry[1] for entry in THERMAL_2030['BA'].values())!=1518.:
        raise ValueError('BA available net MW do not match the 2030 roster')
    return aliases


def install_hr_dispatchable_roster(rows,stock,root):
    base={(r['unit_name'],r['Zone'],r['System']):r for r in rows}
    z,s='HR1','HR'
    aliases,annual={},{}
    gas=root/'gas_stock_2025_preflight'
    gas_meta=read_json(gas/'preflight.json')
    gas_rows=csv_rows(read_pinned(gas/'candidate_generator_rows.csv',gas_meta['output_sha256']))
    for original in ('TE-TO_Zagreb','EL-TO_Zagreb','TE_-TO_Osijek','KTE_Jertovec'):
        stock[original,z,s]=0.
    for r in gas_rows:
        name=r['unit_name']
        if name in ('HRJertovec2020',gas_meta['expansion']['unit']):
            continue
        origin='KTE_Jertovec' if name=='HREL_TO_Zagreb_L' else 'TE-TO_Zagreb'
        template=base[origin,z,s]
        if any(k[0]==name for k in base) or name in aliases or r['fuel_type']!='Gas':
            raise ValueError('Unexpected or colliding HR gas unit: '+name)
        eta=float(r['efficiency'])
        mw=float(r['cap_mw'])
        if not 0.<eta<=1. or mw<=0.:
            raise ValueError('Invalid gas unit parameters')
        rows.append(dict(template,unit_name=name,CHPType='N',efficiency=str(eta),
                         CO2Intensity=str(.202/eta),cost_no_fuel=r['cost_no_fuel'],
                         RampingCost=r['RampingCost'],RampUpRate=r['RampUpRate'],RampDownRate=r['RampDownRate']))
        aliases[name]=origin
        stock[name,z,s]=mw
    if not near(fsum(stock[n,z,s] for n in aliases),gas_meta['available_gas_candidate_mw']):
        raise ValueError('Croatian gas roster did not close the available MW')

    policy=read_json(root/'future_dispatchable_renewables_policy.json')['units']
    for old in ('PZ_Osatina','Bovis','TE_Biomasa_HR','HR_New_Biomass'):
        stock[old,z,s]=0.
    for name,origin in (('HR_Biomass_Existing','TE_Biomasa_HR'),('HR_Biogas_Existing','PZ_Osatina')):
        parameters=policy['TEBiomassHR' if name=='HR_Biomass_Existing' else 'HRBiogas2020']
        if not parameters['active'] or any(k[0]==name for k in base):
            raise ValueError('Unexpected bioenergy row: '+name)
        template=base[origin,z,s]
        rows.append(dict(template,unit_name=name,CHPType='N',fuel_type=parameters['fuel'],
                         efficiency=str(parameters['net_electric_efficiency']),CO2Intensity='0.0'))
        aliases[name]=origin
        stock[name,z,s]=parameters['installed_mw']
        annual[name]=parameters['annual_max_mwh']
    return aliases,annual


def prepare_candidate(raw, root, *, thermal_2030=False, hr_dispatchable=False):
    """A staged CSV candidate. Retain all unreconciled rows byte-semantically as source values."""
    if sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError('Wrong electricity-only donor inventory')
    base = electricity_candidate(raw)
    reader = csv.DictReader(StringIO(base.decode('utf8')))
    rows = list(reader)
    stock,cross = supported_stock(root)
    original_capacity={(r['unit_name'],r['Zone'],r['System']):(float(r['cap_mw']),float(r['max_cap'])) for r in rows}
    aliases=install_thermal_roster(rows,stock) if thermal_2030 else {}
    annual={}
    if hr_dispatchable:
        if not thermal_2030:
            raise ValueError('Use the common dated thermal inventory in all zones')
        hr_aliases,annual=install_hr_dispatchable_roster(rows,stock,root)
        aliases.update(hr_aliases)
    receipt = {}
    for row in rows:
        unit,z,system = key = row['unit_name'],row['Zone'],row['System']
        if key not in stock:
            continue
        if key in receipt:
            raise ValueError('Duplicate dated capacity row: '+repr(key))
        old_stock,old_total = original_capacity.get(key,(0.,0.))
        keep_weather_ceiling = system=='HR' and unit.startswith(WEATHER_BUILD)
        dated = stock[key]
        total = old_total if keep_weather_ceiling else dated
        if total+TOL<dated:
            raise ValueError('Dated capacity exceeds the unchanged ceiling: '+repr(key))
        if key in FLEET:
            apply_hydro_stock(row)
        else:
            row.update(cap_mw=str(dated),max_cap=str(total))
        receipt[key] = dict(existing_mw=dated,total_ceiling_mw=total,
                            allows_legacy_weather_build=keep_weather_ceiling,
                            source_existing_mw=old_stock,source_total_ceiling_mw=old_total,
                            technology=row['technology'],fuel=row['fuel_type'],
                            efficiency=float(row['efficiency']),co2_t_per_mwh=float(row['CO2Intensity']))
    if set(receipt) != set(stock):
        raise ValueError('Missing native generator names: '+repr(set(stock)-set(receipt)))
    buf = StringIO(newline='')
    writer = csv.DictWriter(buf,reader.fieldnames,lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    data = buf.getvalue().encode('utf8')
    pending=dict(PENDING)
    if thermal_2030:
        pending.pop('SI')
        pending['BA']=('Do not automatically treat the industrial and waste candidates as firm grid generation.',)
    if hr_dispatchable:
        pending['HR']=('Qualify the zonal CAPEX conversion and policy for the optional new CCGT before permitting any investment in it.',)
    return data,dict(status='partial_dated_inventory_not_approved',planning_approved=False,
                     source_sha256=SOURCE_SHA,candidate_sha256=sha256(data).hexdigest(),
                     rows=[dict(unit=u,zone=z,system=s,**entry) for (u,z,s),entry in sorted(receipt.items())],
                     future_stock_pending=pending,thermal_cost_aliases=aliases,annual_power_ceiling_mwh=annual,
                     thermal_2030=({'ba_existing_coal_mw':1518.,'si_ppe_tol_mw':132.,'references':THERMAL_REFERENCES,
                                    'assumptions':[
                                       'Heat-rate conversion is 3600 kJ/kWh divided by the published net heat rate.',
                                       'Stanari net efficiency 0.34 and PPE-TOL 0.50 are scenario values.',
                                       'Keep native fuel-side coal emission proxy and native fixed-cost archetypes; no new thermal investment.',
                                    ]} if thermal_2030 else None),
                     references={'vinca_excluded_from_slovenia':'https://disclosures.ifc.org/project-detail/ESRS/37838/belgrade-wte',
                                 'capljina_pumping':'https://henatrebisnjici.com/sistem/phe-capljina/?pismo=lat'},
                     ba_coal_existing_mw_candidate=fsum(cross['BA']['mapped_or_named_mw'][u]
                         for u in ('TE_Kakanj','TE_Ugljevik','TE_Gacko'))+fsum(cross['BA']['separate_physical_coal_mw'].values()))


def configure_known_build_limits(ctx, report):
    """Use after reading the candidate CSV, before defining model variables."""
    costs=ctx['techology_cap_costs_years']
    for unit,origin in report.get('thermal_cost_aliases',{}).items():
        if (unit,YEAR) in costs or (origin,YEAR) not in costs:
            raise ValueError('Unexpected native fixed-cost mapping for '+unit)
        costs[unit,YEAR]=costs[origin,YEAR]
    for row in report['rows']:
        key = row['unit'],row['zone'],row['system']
        expected = row['existing_mw']
        if (not near(ctx['capacity_generator'][key],expected) or not near(ctx['max_load'][key],expected)
                or not near(ctx['capacity_generator_max'][key],row['total_ceiling_mw'])):
            raise ValueError('Dated generator stock did not reach the model reader: '+repr(key))
        if not row['allows_legacy_weather_build']:
            ctx['cap_inv_lim_long_lim'][row['unit'],YEAR,row['zone']] = 0.
        if (row['unit'] in report.get('thermal_cost_aliases',{}) or
                (report.get('thermal_2030') and row['unit'] in THERMAL_2030.get(row['system'],{}))):
            if (not near(ctx['eff_generator'][key],row['efficiency'])
                    or not near(ctx['CO2_factor'][row['unit']],row['co2_t_per_mwh'])
                    or (row['unit'],YEAR) not in ctx['var_cost']):
                raise ValueError('Dated thermal coefficients did not reach native inputs: '+repr(key))


def install_zonal_balance_roster(ctx,report):
    """Admit only source-qualified positive dispatchable rows, not every legacy waste row."""
    if tuple(ctx['years'])!=(YEAR,) or ctx['model_transport_and_industry'] or ctx['model_efuel']:
        raise ValueError('The dispatch roster override is electricity-only')
    roster=list(ctx['fossil_units'])
    approved={('HR_Biogas_Existing','HR1','HR'):'Biogas',('Waste_SI','SI1','SI'):'Waste'}
    records={}
    for row in report['rows']:
        key=row['unit'],row['zone'],row['system']
        if key not in approved: continue
        u,z,s=key
        if (row['fuel']!=approved[key] or row['existing_mw']<=0 or u in roster
                or dict(ctx['unit_fuel'])[u]!=row['fuel'] or u not in ctx['unit_zone_check'][z]
                or u not in ctx['unit_system_check'][s]
                or not near(ctx['eff_generator'][key],row['efficiency'])
                or not near(ctx['CO2_factor'][u],row['co2_t_per_mwh'])):
            raise ValueError('Qualified generator parameters differ: '+repr(key))
        cost=float(ctx['var_cost'][u,YEAR])
        if not isfinite(cost):
            raise ValueError('Invalid fuel-based generation cost: '+u)
        roster.append(u)
        records[u]=dict(system=s,zone=z,fuel=row['fuel'],existing_mw=row['existing_mw'],
                        efficiency=row['efficiency'],co2_t_per_mwh=row['co2_t_per_mwh'],native_var_cost=cost)
    if set(records)!={k[0] for k in approved} or len(roster)!=len(set(roster)):
        raise ValueError('Incorrect number of qualified grid roster additions')
    ctx['fossil_units']=roster
    ctx['_qualified_zonal_dispatch']=records


def audit_known_native_stock(ctx, report, labels):
    """Check the actual base expression, total ceiling and new-build rows."""
    if tuple(ctx['years']) != (YEAR,) or tuple(ctx['previous_years_list']):
        raise ValueError('Expected a one-window dated inventory without previous investments')
    model = ctx['ucp']
    model.update()
    def terms(expr):
        result = {}
        for i in range(expr.size()):
            col = expr.getVar(i).index
            result[col] = result.get(col,0.)+expr.getCoeff(i)
        return {i:v for i,v in result.items() if not near(v,0.)}
    def named(column,label):
        matches = [column.getConstr(i) for i in range(column.size())
                   if column.getConstr(i).ConstrName.startswith(label+'[')]
        if len(matches)!=1:
            raise ValueError('Native generator cap row missing or duplicated: '+label)
        return matches[0]
    for row in report['rows']:
        unit,z,system = row['unit'],row['zone'],row['system']
        key = unit,z,system,YEAR
        investment = ctx['cap_inv'][key]
        available = ctx['available_capacity_year'][key]
        column = model.getCol(investment)
        if not near(available.getConstant(),row['existing_mw']) or terms(available)!={investment.index:1.}:
            raise ValueError('Unexpected native stock/carry/investment at '+repr(key))
        for family,rhs in (
            ('max_generator_capacity',row['total_ceiling_mw']-available.getConstant()),
            ('max_inv_unit',ctx['cap_inv_lim_long_lim'][unit,YEAR,z]),
        ):
            cap = named(column,labels[family])
            if cap.Sense!='<' or not near(cap.RHS,rhs) or terms(model.getRow(cap))!={investment.index:1.}:
                raise ValueError('Native capacity constraint differs: '+repr((key,family)))
    annual_rows={}
    for unit,limit in report.get('annual_power_ceiling_mwh',{}).items():
        generation=[ctx['generation'][unit,'HR1','HR',p,YEAR] for p in ctx['periods']]
        if len(generation)!=8760:
            raise ValueError('Expected one physical MWh per hour for the bioenergy ceiling')
        annual_rows[unit]=(model.addConstr(ctx['gp'].quicksum(generation)<=limit,
                                         name=f'zonal_bioenergy_limit[{unit}]'),generation,limit)
    model.update()
    for unit,(row,variables,limit) in annual_rows.items():
        if row.Sense!='<' or not near(row.RHS,limit) or model.getRow(row).size()!=8760 or any(
                not near(model.getCoeff(row,v),1.) for v in variables):
            raise ValueError('Native bioenergy resource cap differs: '+unit)
    return dict(checked_dated_units=len(report['rows']),production_approved=False,
                annual_power_ceiling_mwh={k:v[2] for k,v in annual_rows.items()})


def dispatch_row_from_column(model,var,identity):
    column=model.getCol(var)
    rows=[column.getConstr(i) for i in range(column.size())]
    matches=[r for r in rows if r.ConstrName.startswith('max_output_dispatch[')]
    if len(matches)!=1:
        raise ValueError(f'Expected exactly one hourly dispatch cap for {identity}; found {len(matches)}; '
                         f'column rows: {[r.ConstrName for r in rows]}')
    return matches[0]


def audit_zonal_balance_roster(ctx):
    model=ctx['ucp']
    model.update()
    records=ctx['_qualified_zonal_dispatch']
    for u,row in records.items():
        z,s=row['zone'],row['system']
        carbon=ctx['CO2_emissions_power_for_PP'][s,YEAR]
        basis=model.getCoeff(carbon,ctx['CO2_total_PP'][YEAR,s])
        if abs(basis)!=1.:
            raise ValueError('The emissions accounting basis changed: '+u)
        investment=ctx['cap_inv'][u,z,s,YEAR]
        if (u,z,s) not in ctx['dispatchable_only']:
            raise ValueError('Dated dispatchable unit absent from the native dispatch index: '+u)
        for p in ctx['periods']:
            v=ctx['generation'][u,z,s,p,YEAR]
            cap=dispatch_row_from_column(model,v,(u,z,s,p,YEAR))
            if (model.getCoeff(ctx['energy_balance'][z,s,p,YEAR],v)!=1.
                    or not near(-model.getCoeff(carbon,v)/basis,row['co2_t_per_mwh'])
                    or cap.Sense!='<' or not near(cap.RHS,row['existing_mw'])
                    or model.getRow(cap).size()!=2 or model.getCoeff(cap,v)!=1.
                    or model.getCoeff(cap,investment)!=-1.):
                raise ValueError(f'Dispatch membership, emissions or native cap did not close: {u}/{p}')
    return dict(units=records,rows=len(records)*len(ctx['periods']))


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thermal-2030',action='store_true')
    parser.add_argument('--hr-dispatchable',action='store_true')
    args=parser.parse_args()
    root = Path(__file__).resolve().parent
    data,report = prepare_candidate(SOURCE.read_bytes(),root,thermal_2030=args.thermal_2030,
                                    hr_dispatchable=args.hr_dispatchable)
    out = root/('zonal_2030_stock_dispatchable_preflight' if args.hr_dispatchable else
                'zonal_2030_stock_with_thermal_preflight' if args.thermal_2030 else 'zonal_2030_stock_with_hydro_preflight')
    out.mkdir(exist_ok=False)
    (out/'candidate_genco.csv').write_bytes(data)
    (out/'preflight.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf8')

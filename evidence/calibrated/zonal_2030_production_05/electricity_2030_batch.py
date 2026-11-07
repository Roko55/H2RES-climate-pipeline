"""Run the corrected full-hour zonal electricity experiment under one controller."""
import ast
import csv
from datetime import datetime, timezone
import json
from math import fsum, isfinite
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback

import electricity_matrix_2030 as matrix
from electricity_project_integration import digest_file
from eraa_electricity_integration import audit_solved_eraa_2030


ROOT = Path(__file__).resolve().parent
CASES = ('avg','low','high')
HISTORY = tuple(range(2020,2025))
FRACTIONS = (1.,.5,0.)
TOL = 1e-7
HISTORICAL_SHA = 'b700263a2868b20cc8f7fe95e0a7d22c48ea31c89cedbc0889a55ca870972d8a'
CONTROL_FINGERPRINT = -2078809620
INVEST = ('cap_inv','cap_inv_FC','h2_electrolysis_max_cap','h2_sto_max_cap','new_H2_steam',
          'boiler_industry_cap_inv','CC_industry_cap_inv','CC_tank_cap_inv','CC_reboiler_cap_inv',
          'CO2_storage_cap_inv','stat_sto_cap_inv','fuel_storage_cap_inv','heat_pump_ind_cap',
          'heat_pump_dh_cap','trad_boiler_ind_cap_inv','trad_boiler_dh_cap_inv','veh_inv',
          'efuel_gen_new_cap_gen','transfer_cap_new','transfer_cap_outside_new',
          'transfer_cap_new_H2','transfer_cap_outside_new_H2','transfer_cap_new_terminal')


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf8')
    for _ in range(20):
        try:
            pending.replace(path)
            return
        except PermissionError:
            time.sleep(.1)
    raise OSError('Could not publish '+str(path))


def load(path):
    return json.loads(path.read_bytes())


def owned_path(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Missing input or input outside frozen source: '+str(path))
    return path


def weather_inputs(paths, inputs, label, weather, *, validation_only=False):
    """Use archived spatial weather input only, never an archived dispatch or cost."""
    runtime = str(matrix.REMOTE/'energy_rebuild_20261019/runtime_packages')
    sys.path.insert(0,runtime)
    import numpy as np
    import pandas as pd

    archive = matrix.ARCHIVE
    donor = archive/'stress'/f'avg_{weather}_1p0'
    audit = load(donor/'audit.json')
    marker = load(donor/'accepted.json')
    if (audit['historical_year'] != weather or audit['hours'] != 8760
            or digest_file(donor/'audit.json') != marker['files']['audit.json']
            or marker['fence'] != load(archive/'preparation.json')['fence']):
        raise ValueError('Historical donor is not in the verified archive')
    pkg_dir = donor/'inputs'/str(weather)/'avg_yield__normal'
    spec = load(pkg_dir/'package_specification.json')
    weather_ncre = owned_path(spec['ncre_path'],pkg_dir)
    summary_path = owned_path(spec['summary_path'],pkg_dir)
    summary = load(summary_path)
    profile_path = owned_path(summary['climate_profile_path'],pkg_dir)
    profile = pd.read_csv(profile_path)
    native = pd.read_csv(paths['ncre_path'])
    candidate = pd.read_csv(weather_ncre)
    section = native['year'].eq(2030)
    if (summary['historical_weather_year'] != weather or list(native.loc[section,'period']) != list(matrix.HOURS)
            or list(candidate['period']) != list(matrix.HOURS)
            or list(profile['period']) != list(matrix.HOURS) or set(profile['year']) != {2030}):
        raise ValueError('Historical input does not provide the full 2030 hourly calendar')
    raw = archive/'profiles/HR_data.parquet'
    raw_sha = digest_file(raw)
    if raw_sha != HISTORICAL_SHA:
        raise ValueError('Historical archive does not match the verified source hash')
    history = pd.read_parquet(raw,columns=['time','solar','wind','temperature'],engine='pyarrow')
    dates = pd.to_datetime(history['time'])
    reference = history.loc[dates.dt.year.eq(weather) & ~((dates.dt.month == 2) & (dates.dt.day == 29))]
    if len(reference) != 8760 or not all(np.allclose(profile[dst],reference[src],rtol=0,atol=1e-12)
            for dst,src in (('solar_cf','solar'),('wind_cf','wind'),('temperature_c','temperature'))):
        raise ValueError('Prepared stress weather differs from frozen historical source')
    genco = pd.read_csv(paths['genco_path'])
    replaced = []
    for technology, token, channel in (('solar','HR_Solar_','solar_cf'),('wind','HR_Wind_','wind_cf')):
        units = genco.loc[genco['unit_name'].str.startswith(token) & genco['System'].eq('HR')]
        names = list(units['unit_name'])
        if len(names) != (3 if technology == 'solar' else 5):
            raise ValueError('Unexpected spatial generator scope')
        values = candidate[names].to_numpy(dtype=float)
        if not np.isfinite(values).all() or np.any(values<0) or np.any(values>1):
            raise ValueError('Invalid historical spatial capacity factors')
        if not np.allclose(np.average(values,axis=1,weights=units['cap_mw']),profile[channel],rtol=0,atol=1e-8):
            raise ValueError('Historical spatial curve fails hourly national closure: '+technology)
        native.loc[section,names] = values
        replaced.extend(names)
    audit = dict(historical_year=weather,raw_sha256=raw_sha,ncre_sha256=digest_file(weather_ncre),
                 profile_sha256=digest_file(profile_path),changed_hr_weather_columns=replaced)
    if validation_only:
        return audit
    native.to_csv(paths['ncre_path'],index=False,float_format='%.17g')
    paths['profile_path'].write_bytes(profile_path.read_bytes())
    own_summary = load(paths['summary_path'])
    own_summary.update(historical_weather_year=weather,climate_profile_path=str(paths['profile_path']))
    save(paths['summary_path'],own_summary)
    for key in ('ncre_path','summary_path','profile_path'):
        target = paths[key]
        inputs[target.relative_to(matrix.PROJECT).as_posix()]['staged_sha256'] = digest_file(target)
    save(matrix.ROOT/'input_manifest.json',inputs)
    return audit


def group_names(project):
    path = project/'scripts/export_stage2_dispatch_bundle.py'
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    match = [node.value for node in tree.body if isinstance(node,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id=='fixed_capacity_groups' for t in node.targets)]
    if len(match) != 1:
        raise ValueError('Missing native full-stock export schema')
    names = ast.literal_eval(match[0])
    if not {'generationcapyear','stat_sto_cap','transfer_cap_new','transfer_cap_outside_new',
            'stat_sto_soc0','storage_level0'} <= set(names):
        raise ValueError('Missing complete fixed-capacity families')
    return sorted(set(names)|set(INVEST))


def vars_for(ctx, name):
    data = ctx.get(name,{})
    if not isinstance(data,dict):
        raise ValueError('Not a keyed native variable group: '+name)
    if any(not hasattr(v,'LB') or not hasattr(v,'UB') for v in data.values()):
        raise ValueError('Non-variable in the stock group: '+name)
    return data


def key_json(key):
    return json.dumps(key,default=lambda v:v.item(),separators=(',',':'))


def snapshot(ctx,names):
    return {name:{key_json(k):float(v.X) for k,v in vars_for(ctx,name).items()} for name in names}


def pin_portfolio(ctx,names,portfolio):
    if set(portfolio) != set(names):
        raise ValueError('Different native fixed-stock families')
    for name in names:
        variables = {key_json(k):v for k,v in vars_for(ctx,name).items()}
        values = portfolio[name]
        if set(variables) != set(values):
            raise ValueError('Different native fixed-stock keys in '+name)
        for key, v in variables.items():
            target = values[key]
            if not isfinite(target) or target < v.LB-TOL or target > v.UB+TOL:
                raise ValueError('Native stock target violates original bounds: '+name)
            v.LB = v.UB = target
    ctx['ucp'].update()


def storage_cycles(ctx):
    m = ctx['ucp']
    groups = {'stat_sto_soc0':('stat_sto_soc',lambda k:(k[0],8760,*k[1:])),
              'storage_level0':('storage_level',lambda k:(k[0],k[1],8760,k[2]))}
    cycles = []
    for group,(ending,convert) in groups.items():
        for key,start in vars_for(ctx,group).items():
            end = ctx[ending][convert(key)]
            m.addConstr(end == start,name=f'fixed_annual_soc_{group}[{len(cycles)}]')
            cycles.append((start,end))
    if not cycles:
        raise ValueError('No annual electric storage cycles')
    m.update()
    return cycles


def operational_meter(ctx):
    """Report availability, service and the original CEEP accounting separately."""
    def hr_hours(name):
        group = ctx[name]
        hr = {int(p):float(v.X) for (p,y,z,s),v in group.items()
              if y==2030 and (z,s)==('HR1','HR')}
        if set(hr)!=set(matrix.HOURS) or not all(isfinite(x) and x>=-TOL for x in hr.values()):
            raise ValueError('Incomplete or negative Croatian result: '+name)
        return hr

    out = {}
    for label, name in (('unserved','noloadel'),('curtailed','ceep')):
        series = hr_hours(name)
        out[label+'_mwh'] = fsum(max(0.,v) for v in series.values())
        out[label+'_hours'] = sum(v>TOL for v in series.values())
    boundary = (('import_zone_system','in',True),('export_zone_system','out',True),
                ('import_outside','in',False),('export_outside','out',False))
    totals = {'in':[],'out':[]}
    for name,direction,primary in boundary:
        for key,v in ctx[name].items():
            unit,y,period,zone,system,*tail = key
            if (y==2030 and (zone,system)==('HR1','HR')
                    and ((primary and tail[-1]!='HR' and unit in ctx['transmission_units_el'])
                         or (not primary and unit in ctx['transmission_outside_units_el']))):
                if not isfinite(v.X) or v.X < -TOL:
                    raise ValueError('Negative gross directional flow: '+name)
                totals[direction].append(max(0.,v.X))
    if not all(totals.values()):
        raise ValueError('Missing Croatian gross interchange')
    charge,discharge,simultaneous = [],[],[]
    hours = set()
    for (unit,p,y,z,s),v in ctx['stat_charge'].items():
        if y!=2030 or (z,s)!=('HR1','HR'):
            continue
        internal = ctx['stat_discharge'][unit,p,y,z,s].X
        eta = ctx['Sta_sto_eff'][unit,z,s]
        a,b = float(v.X),float(internal)*eta
        if not all(isfinite(x) and x>=-TOL for x in (a,b)) or not 0.<eta<=1.:
            raise ValueError('Invalid Croatian battery flow or conversion')
        charge.append(max(0.,a))
        discharge.append(max(0.,b))
        if min(a,b)>TOL:
            simultaneous.append(min(a,b))
            hours.add(p)
    active = bool(ctx['ceep_limit'])
    ceep_share = float(ctx['CEEP_lim']) if active else None
    if active and not (isfinite(ceep_share) and 0.<=ceep_share<=1.):
        raise ValueError('Unexpected Croatian CEEP cap fraction')
    out.update(gross_import_mwh=fsum(totals['in']),gross_export_mwh=fsum(totals['out']),
               stationary_grid_charge_mwh=fsum(charge),stationary_grid_discharge_mwh=fsum(discharge),
               stationary_simultaneous_hours=len(hours),stationary_simultaneous_grid_overlap_mwh=fsum(simultaneous),
               ceep_limit_active=active,ceep_limit_fraction=ceep_share,
               ceep_allowance_from_gross_stationary_charging_mwh=ceep_share*fsum(charge) if active else None,
               ceep_allowance_from_simultaneous_overlap_mwh=ceep_share*fsum(simultaneous) if active else None)
    return out


def optimize_checked(m):
    m.Params.Threads = 4
    m.Params.Method = 2
    m.Params.Crossover = 0
    m.Params.TimeLimit = 900
    attempts = []
    def attempt():
        m.optimize()
        attempts.append(dict(method=m.Params.Method,status=int(m.Status),runtime_seconds=m.Runtime))
    attempt()
    if m.Status == 4:
        m.Params.DualReductions = 0
        attempt()
    if m.Status in (12,13) or (m.Status == 2 and max(m.ConstrVio,m.BoundVio)>TOL):
        m.reset()
        m.Params.Method = 1
        m.Params.FeasibilityTol = m.Params.OptimalityTol = 1e-9
        m.Params.ScaleFlag = 2
        m.Params.TimeLimit = 600
        attempt()
    return attempts


def solve(ctx, destination):
    m = ctx['ucp']
    before = m.Fingerprint
    attempts = optimize_checked(m)
    if m.Fingerprint != before:
        raise ValueError('Solver recovery changed the mathematical model')
    q = dict(solver_status=m.Status,solutions=m.SolCount,runtime_seconds=m.Runtime,fingerprint=m.Fingerprint)
    q['attempts'] = attempts
    save(destination/'solver_status.json',q)
    if m.Status == 3:
        m.Params.TimeLimit = 600
        m.computeIIS()
        m.write(str(destination/'infeasible.ilp'))
        q['iis_sha256'] = digest_file(destination/'infeasible.ilp')
    elif m.Status != 2 or not all(isfinite(v) and 0.<=v<=TOL for v in (m.ConstrVio,m.BoundVio)):
        raise ValueError(f'Solver quality not accepted: {q}')
    else:
        q.update(objective_model_units=m.ObjVal,bound_violation=m.BoundVio,
                 constraint_violation=m.ConstrVio)
        variables = m.getVars()
        coeffs, values = (m.getAttr(attr,variables) for attr in ('Obj','X'))
        total = fsum(c*x for c,x in zip(coeffs,values))+float(m.getObjective().getConstant())
        if abs(total-m.ObjVal)>=.01:
            raise ValueError('Solved objective coefficients do not close')
        q['objective_closure_error'] = total-m.ObjVal
    return q


def worker(label, weather=None, fraction=1.):
    job = ROOT/'jobs'/job_name(label,weather,fraction)
    job.mkdir(parents=True,exist_ok=False)
    matrix.ROOT,matrix.PROJECT,matrix.STATUS = job,job/'project',job/'build_status.json'
    for rel in ('candidate_hourly_mw.csv','preflight.json'):
        source = ROOT/rel
        (job/rel).write_bytes(source.read_bytes())
    try:
        if label != 'avg' or weather is not None:
            raise ValueError('Only the Average control is permitted: the inherited physical state and meter boundary are not verified')
        paths,inputs,scenario,year_specific = matrix.stage(label,full_handoff=True)
        weather_audit = weather_inputs(paths,inputs,label,weather) if weather is not None else None
        ctx = matrix.assemble(paths,scenario,year_specific,label,fraction,matrix_only=False)
        if ctx is None:
            raise RuntimeError('Server admission lost before model creation')
        preflight = matrix.check_matrix(ctx,paths,inputs)
        save(job/'matrix.json',preflight)
        if label == 'avg' and weather is None and preflight['fingerprint'] != CONTROL_FINGERPRINT:
            raise ValueError('The numerical recovery control did not reproduce the previous matrix')
        names = group_names(matrix.PROJECT)
        parent_sha = None
        cycles = []
        if weather is not None:
            parent = ROOT/'jobs'/job_name(label)
            a = load(parent/'accepted.json')
            path = parent/'portfolio.json'
            parent_sha = digest_file(path)
            if a['status']!='optimal_planning' or parent_sha!=a['portfolio_sha256']:
                raise ValueError('No verified corrected 2030 portfolio')
            pin_portfolio(ctx,names,load(path))
            cycles = storage_cycles(ctx)
        q = solve(ctx,job)
        a = dict(case=label,weather=weather,hr_exchange_fraction=fraction,solver=q,
                 inputs_sha256=digest_file(job/'input_manifest.json'),exchange_source_sha256=digest_file(job/'candidate_hourly_mw.csv'),
                 matrix_sha256=digest_file(job/'matrix.json'),historical_input=weather_audit,parent_portfolio_sha256=parent_sha,
                 model_year=2030,hours=8760,utc=utc(),no_new_investment_decisions=weather is not None)
        if q['solver_status']==3:
            if weather is None:
                raise ValueError('A planning portfolio is infeasible')
            a['status']='verified_infeasible_operation'
        else:
            a['status']='optimal_control' if weather is None else 'optimal_operation'
            a['exchange'] = audit_solved_eraa_2030(ctx)
            stock = snapshot(ctx,names)
            save(job/'portfolio.json',stock)
            a['portfolio_sha256'] = digest_file(job/'portfolio.json')
            a['unserved_mwh_by_system'] = {s:fsum(v.X for k,v in ctx['noloadel'].items() if k[-1]==s)
                                          for s in ('HR','SI','BA')}
            if weather is not None:
                parent = load(ROOT/'jobs'/job_name(label)/'portfolio.json')
                max_delta = max(abs(v-parent[n][k]) for n,group in stock.items() for k,v in group.items())
                cycle_error = max(abs(a.X-b.X) for a,b in cycles)
                if max_delta>TOL or cycle_error>TOL:
                    raise ValueError('Solved operation violated fixed stock or annual storage cycle')
                a.update(max_fixed_stock_delta=max_delta,cycle_count=len(cycles),max_storage_cycle_error=cycle_error)
                a['croatia_meter'] = operational_meter(ctx)
            a['investment_variable_cost_model_units'] = fsum(v.X*v.Obj for n in INVEST for v in vars_for(ctx,n).values())
            if weather is not None:
                a['operating_objective_excluding_fixed_investment_model_units'] = q['objective_model_units']-a['investment_variable_cost_model_units']
        a['eligible_for_fixed_portfolio_tests'] = False
        save(job/'control_result.json',a)
    except BaseException:
        save(job/'failure.json',dict(utc=utc(),error=traceback.format_exc()))
        raise


def job_name(label, weather=None, fraction=1.):
    return 'planning_'+label if weather is None else f'operation_{label}_{weather}_{int(fraction*100)}'


def tasks():
    return ([(label,None,1.) for label in CASES]+
            [(label,year,fraction) for year in HISTORY for fraction in FRACTIONS for label in CASES])


def control():
    import psutil
    matrix.ROOT = ROOT
    state = dict(utc=utc(),status='waiting',completed=[],controller_pid=os.getpid(),
                 controller_created=psutil.Process().create_time(),scope='2030 conditional on archived 2020 and 2025 handoff',
                 old_results_reused_as_new=False)
    try:
        for label,weather,fraction in [('avg',None,1.)]:
            name = job_name(label,weather,fraction)
            state.update(current=name,worker=None)
            while True:
                resources = matrix.resource_check()
                state.update(utc=utc(),status='waiting_for_resources',resources=resources)
                save(ROOT/'status.json',state)
                if resources['ready']:
                    break
                time.sleep(60)
            cmd = [sys.executable,'-u','-B',__file__,'worker',label,str(weather or 0),str(fraction)]
            with (ROOT/(name+'.log')).open('xb') as log:
                child = subprocess.Popen(cmd,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                state.update(utc=utc(),status='running',worker=dict(pid=child.pid,created=psutil.Process(child.pid).create_time(),script=__file__))
                save(ROOT/'status.json',state)
                rc = child.wait()
            accepted = ROOT/'jobs'/name/'control_result.json'
            if rc != 0 or not accepted.exists():
                raise RuntimeError('Stopped on unaccepted job '+name)
            state['completed'].append(dict(job=name,sha256=digest_file(accepted),outcome=load(accepted)['status']))
            state.update(utc=utc(),worker=None)
            save(ROOT/'status.json',state)
        with (ROOT/'results.csv').open('x',encoding='utf8',newline='') as out:
            fields = ('case','weather','hr_exchange_fraction','status','hours','portfolio_sha256',
                      'parent_portfolio_sha256','operating_objective_excluding_fixed_investment_model_units')
            w = csv.DictWriter(out,fields,extrasaction='ignore')
            w.writeheader()
            w.writerows(load(ROOT/'jobs'/item['job']/'control_result.json') for item in state['completed'])
        state.update(status='control_completed_production_blocked',current=None,utc=utc(),
                     results_sha256=digest_file(ROOT/'results.csv'))
        save(ROOT/'status.json',state)
    except BaseException:
        state.update(status='stopped',utc=utc(),error=traceback.format_exc())
        save(ROOT/'status.json',state)
        raise


if __name__ == '__main__':
    if ROOT.parent != matrix.REMOTE or not ROOT.name.startswith('electricity_2030_batch_') or socket.gethostname().lower()!=matrix.EXPECTED_HOST:
        raise RuntimeError('This experiment runs only in its isolated server folder')
    if sys.argv[1]=='worker':
        worker(sys.argv[2],int(sys.argv[3]) or None,float(sys.argv[4]))
    elif sys.argv[1]=='control':
        control()

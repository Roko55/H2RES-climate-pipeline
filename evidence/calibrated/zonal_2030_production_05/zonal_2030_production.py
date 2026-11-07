"""Sequential, resumable planning and fixed-portfolio zonal dispatch."""

from contextlib import contextmanager
from math import fsum, isclose, isfinite
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback

import electricity_2030_batch as batch
import electricity_matrix_2030 as matrix
from eraa_electricity_integration import audit_solved_eraa_2030
from zonal_2030_boundaries import audit_neighbor_rows
from zonal_2030_hourly import save_hourly
from zonal_2030_matrix_queue import TERMINAL, merge_weather, physical_hooks, recovery_state, validate_staged
from zonal_2030_weather import digest


ROOT=Path(__file__).resolve().parent
SCRIPT=Path(__file__).resolve()
DIAGNOSTIC=ROOT.parent/'zonal_2030_matrix_06'
TOL=1e-7
CAPEX_TEMPLATES={'HR_PV':.504,'HR_Wind':.99,'Liion_storage':.15}
TASKS=tuple(batch.tasks())
load,save=batch.load,batch.save


def require_owned():
    if socket.gethostname().lower()!=matrix.EXPECTED_HOST or ROOT.parent!=matrix.REMOTE.resolve() or not ROOT.name.startswith('zonal_2030_production_'):
        raise ValueError('Only the isolated remote production folder is permitted')


def matching(a,b):
    return isfinite(a) and isfinite(b) and isclose(a,b,rel_tol=0.,abs_tol=TOL)


def audit_capex(ctx,stock):
    model=ctx['ucp']
    model.update()
    table=ctx['techology_cap_costs_years']
    for unit,target in CAPEX_TEMPLATES.items():
        if not matching(float(table[unit,2030]),target):
            raise ValueError('Unverified zonal CAPEX template: '+unit)
    result={}
    def check(unit,key,asset,row,cost,life,target):
        factor=min(life,max(0,min(ctx['yearlast'],2030+life-1)-2030+1))/life
        coeff=model.getCoeff(row,asset)
        sign=model.getCoeff(row,cost)
        if factor<=0 or row.Sense!='=' or not matching(abs(sign),1.) or not matching(-coeff/sign,target*factor):
            raise ValueError('Invalid built investment charge: '+unit)
        result[key]=dict(source_model_units=target,active_row_coefficient=abs(coeff),life_fraction=factor)
    expected={r['unit'] for r in stock['rows'] if r['allows_legacy_weather_build']}
    if len(expected)!=8 or 'HRNewGasCCGT' in ctx['unit_zone_check']['HR1']:
        raise ValueError('Unexpected renewable scope or an unapproved new CCGT')
    for (u,z,s,y),v in ctx['cap_inv'].items():
        permitted=ctx['cap_inv_lim_long_lim'][u,y,z]
        if u not in expected or (z,s)!=('HR1','HR'):
            ceiling=ctx['capacity_generator_max'][u,z,s]-ctx['available_capacity_year'][u,z,s,y].getConstant()
            if not matching(max(0.,min(float(permitted),float(ceiling))),0.):
                raise ValueError('Unqualified generator investment allowance: '+repr((u,z,s,y)))
            continue
        target=CAPEX_TEMPLATES['HR_PV' if u.startswith('HR_Solar_') else 'HR_Wind']
        if (not matching(float(table[u,y]),target) or not isfinite(permitted) or permitted<=0
                or u in ctx['units_disp_no_CHP']):
            raise ValueError('Missing, duplicate or invalid spatial CAPEX/limit: '+u)
        check(u,u,v,ctx['inv_cost_years'][y,z,s],ctx['inv_cost_year'][y,z,s],ctx['life_new'][u],target)
    for (u,y,z,s),v in ctx['stat_sto_cap_inv'].items():
        if u!='Liion_storage' or y!=2030:
            raise ValueError('Unknown battery CAPEX key')
        check(u,u+'_'+s,v,ctx['statCapInvCost_years'][y,z,s],ctx['statCapInvCost_year'][y,z,s],
              ctx['flex_life_new'][u],CAPEX_TEMPLATES[u])
    if set(result)!=expected|{'Liion_storage_'+s for s in ('HR','SI','BA')}:
        raise ValueError('Incomplete zonal renewable and battery CAPEX audit')
    return result


def objective_accounting(ctx,q):
    capex=fsum(v.X*v.Obj for v in ctx['CAPEX_year'].values())
    fixed=fsum(v.X*v.Obj for v in ctx['fix_OPEX_year'].values())
    variable=fsum(expr.getValue() for expr in ctx['variable_OPEX_MEUR'].values())
    obj=q['objective_model_units']
    residual=fsum((capex,fixed,variable,-obj))
    if not matching(residual,0.) or not matching(q['objective_closure_error'],0.):
        raise ValueError(f'Native CAPEX/OPEX do not close the objective: {residual}')
    return dict(capex=capex,fixed_opex=fixed,variable_opex=variable,operating_excluding_capex=fixed+variable,
                closure_error=residual,unit='native scaled objective units')


def meter(ctx):
    result={}
    for s,z in (('HR','HR1'),('SI','SI1'),('BA','BA1')):
        streams={}
        for label,group in (('eens','noloadel'),('ceep','ceep')):
            values=[float(ctx[group][p,2030,z,s].X) for p in matrix.HOURS]
            if any(not isfinite(x) or x< -TOL for x in values):
                raise ValueError('Invalid electrical service or excess metric')
            streams[label+'_mwh']=fsum(max(0.,v) for v in values)
            streams[label+'_hours']=sum(v>TOL for v in values)
        result[s]=streams
    return result


def gate():
    state=load(DIAGNOSTIC/'queue_status.json')
    if state['stage']=='stopped':
        raise ValueError('The updated matrix diagnostic stopped; no production solver authorized')
    if state['stage']!='native_diagnostic_completed':
        return None
    report_path=DIAGNOSTIC/'matrix_report.json'
    report=load(report_path)
    p=report['physical']
    if (report['no_optimization'] is not True or report['production_approved'] is not False
            or report['native']['fingerprint']!=state['fingerprint'] or report['native']['zone_balances']!=26280
            or p['generation']['checked_dated_units']!=61 or p['si_ba_directional_rows']!=35040
            or p['generation']['power_balance_roster']['rows']!=17520
            or set(p['generation']['power_balance_roster']['units'])!={'HR_Biogas_Existing','Waste_SI'}
            or p['battery']['power_rows']!=26280 or len(p['hydro'])!=8):
        raise ValueError('Incomplete native electricity matrix receipt')
    _,stock,_,_=validate_staged()
    manifest=load(DIAGNOSTIC/'input_manifest.json')
    genco=report['native']['active_native_inputs']['genco_dat'].replace('\\','/')
    if digest(stock)!=manifest[genco]['staged_sha256'] or digest(DIAGNOSTIC/'project'/genco)!=digest(stock):
        raise ValueError('Matrix diagnosis used a different stock input')
    return digest(report_path),state['fingerprint']


def national_recovery_finished():
    root=ROOT.parent/'national_production_03'
    return (recovery_state(root,'recovery_02') in TERMINAL
            and recovery_state(root,'recovery_03') in TERMINAL|{'not_launched'})


def pin_key(label,weather,fraction):
    if (label,weather,fraction) not in TASKS:
        raise ValueError('Unknown planning/operations tuple')
    return batch.job_name(label,weather,fraction)


def receipt(root,label,weather,fraction,fence):
    name=pin_key(label,weather,fraction)
    attempts=root/'jobs'/name
    if not attempts.exists(): return None
    markers=sorted(attempts.glob('attempt_*/accepted.json'))
    if not markers: return None
    if len(markers)!=1:
        raise ValueError('Duplicate accepted attempts: '+name)
    marker,=markers
    a=load(marker)
    if ((a['case'],a['weather'],a['fraction'])!=(label,weather,fraction) or a['diagnostic_sha256']!=fence
            or a['hours']!=8760 or (weather is None and a['status']!='optimal_planning')
            or (weather is not None and a['status'] not in {'optimal_operation','verified_infeasible_operation'})):
        raise ValueError('Foreign or incomplete accepted result: '+name)
    infeasible=a['status']=='verified_infeasible_operation'
    mandatory={'matrix.json','solver_status.json','input_manifest.json',
               'infeasible.ilp' if infeasible else 'portfolio.json'}
    if not infeasible:
        mandatory.add('hourly.csv.gz')
    if (not mandatory<=a['files'].keys() or a['solver']['solver_status']!=(3 if infeasible else 2)
            or (weather is not None and a['no_new_investment_choices'] is not True)):
        raise ValueError('Accepted result is missing mandatory evidence: '+name)
    for rel,expected in a['files'].items():
        if Path(rel).name!=rel or digest(marker.parent/rel)!=expected:
            raise ValueError('Accepted result evidence changed: '+name+'/'+rel)
    if weather is not None:
        parent=receipt(root,label,None,1.,fence)
        if not parent or a['parent_portfolio_sha256']!=parent[0]['files']['portfolio.json']:
            raise ValueError('Wrong accepted parent portfolio: '+name)
    return a,marker


def worker(label,weather,fraction,job):
    fence,fingerprint=gate()
    name=pin_key(label,weather,fraction)
    if job.parent!=ROOT/'jobs'/name or not job.name.startswith('attempt_') or not job.is_dir():
        raise ValueError('Worker attempt leaves its approved directory')
    matrix.ROOT,matrix.PROJECT,matrix.STATUS=job,job/'project',job/'build_status.json'
    for n in ('candidate_hourly_mw.csv','preflight.json'):
        (job/n).write_bytes((ROOT/n).read_bytes())
    weather_root,candidate,stock,streams=validate_staged()
    paths,inputs,scenario,year_specific=matrix.stage(physical_handoff=True)
    paths['genco_path'].write_bytes(candidate.read_bytes())
    overlay=weather_root/((label if weather is None else f'historical_{weather}')+'_ncre.csv')
    merge_weather(paths['ncre_path'],overlay)
    for k in ('genco_path','ncre_path'):
        path=paths[k]
        inputs[path.relative_to(matrix.PROJECT).as_posix()]['staged_sha256']=digest(path)
    save(job/'input_manifest.json',inputs)
    hydro={}
    hooks=physical_hooks(ROOT,stock,streams,hydro)
    ctx=matrix.assemble(paths,scenario,year_specific,fraction=fraction,matrix_only=False,physical_handoff=True,**hooks)
    if ctx is None:
        raise ValueError('Resource admission was lost before model creation')
    try:
        pre=matrix.check_matrix(ctx,paths,inputs)
        if weather is None and label=='avg' and pre['fingerprint']!=fingerprint:
            raise ValueError('Average planning matrix differs from the accepted diagnosis')
        save(job/'matrix.json',dict(native=pre,physical=ctx['physical_zonal_audit'],capex=audit_capex(ctx,stock)))
        names=sorted(set(batch.group_names(matrix.PROJECT))|{'transfer_cap','transfer_cap_outside'})
        parent_sha=None
        if weather is not None:
            parent,parent_marker=receipt(ROOT,label,None,1.,fence)
            parent_sha=parent['files']['portfolio.json']
            fixed=load(parent_marker.parent/'portfolio.json')
            batch.pin_portfolio(ctx,names,fixed)
            cycles=batch.storage_cycles(ctx)
        q=batch.solve(ctx,job)
        evidence=dict(case=label,weather=weather,fraction=fraction,hours=8760,utc=batch.utc(),
                      diagnostic_sha256=fence,weather_overlay_sha256=digest(overlay),solver=q,
                      no_new_investment_choices=weather is not None,parent_portfolio_sha256=parent_sha)
        files=['matrix.json','solver_status.json','input_manifest.json']
        if q['solver_status']==3:
            if weather is None: raise ValueError('Infeasible planning case')
            evidence['status']='verified_infeasible_operation'
            files.append('infeasible.ilp')
        else:
            solved=batch.snapshot(ctx,names)
            save(job/'portfolio.json',solved)
            hourly=save_hourly(job/'hourly.csv.gz',ctx,hydro)
            if hourly['rows']!=26280:
                raise ValueError('Incorrect full-hour persisted result count')
            service,croatia=meter(ctx),batch.operational_meter(ctx)
            for s,result in hourly['zones'].items():
                if (not matching(result['annual']['load_mwh'],ctx['physical_zonal_audit']['demand_annual_mwh'][s])
                        or any(not matching(result['annual'][k],service[s][k]) for k in ('eens_mwh','ceep_mwh'))):
                    raise ValueError('Persisted hourly demand or service metrics differ in '+s)
            hr=hourly['zones']['HR']['annual']
            if (not matching(hr['gross_import_pre_loss_mwh'],croatia['gross_import_mwh']) or
                    not matching(hr['gross_export_sending_mwh'],croatia['gross_export_mwh'])):
                raise ValueError('Persisted Croatian directional interchange differs')
            files.extend(('portfolio.json','hourly.csv.gz'))
            evidence.update(status='optimal_planning' if weather is None else 'optimal_operation',
                objective=objective_accounting(ctx,q),service=service,croatia=croatia,hourly=hourly,
                exchange=audit_solved_eraa_2030(ctx),
                neighbor_exchange=audit_neighbor_rows(ctx['ucp'],ctx['_zonal_neighbor_rows'],solved=True))
            if weather is not None:
                delta=max(abs(v-fixed[n][k]) for n,g in solved.items() for k,v in g.items())
                soc=max(abs(a.X-b.X) for a,b in cycles)
                if not matching(delta,0.) or not matching(soc,0.):
                    raise ValueError('Fixed portfolio or annual storage closure was violated')
                evidence.update(max_fixed_stock_delta=delta,max_storage_cycle_error=soc,storage_cycles=len(cycles))
        evidence['files']={n:digest(job/n) for n in files}
        save(job/'accepted.json',evidence)
    finally:
        ctx['ucp'].dispose()


@contextmanager
def controller_lock():
    import msvcrt
    with (ROOT/'controller.lock').open('a+b') as f:
        f.seek(0)
        msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        yield


def run():
    import psutil
    with controller_lock():
        state=dict(pid=os.getpid(),completed=[],total=len(TASKS))
        def publish(stage,**extra):
            state.update(utc=batch.utc(),stage=stage,**extra)
            save(ROOT/'status.json',state)
        try:
            while True:
                current=gate()
                if current: break
                publish('waiting_for_updated_matrix')
                time.sleep(30)
            fence,_=current
            for label,weather,fraction in TASKS:
                name=pin_key(label,weather,fraction)
                job=ROOT/'jobs'/name
                if not receipt(ROOT,label,weather,fraction,fence):
                    while True:
                        for p in psutil.process_iter(['cmdline']):
                            argv=p.info['cmdline'] or []
                            if str(SCRIPT) in argv and 'worker' in argv:
                                break
                        else:
                            p=None
                        if p is None: break
                        publish('waiting_for_previous_worker',current=name,worker=p.pid)
                        time.sleep(30)
                    if not receipt(ROOT,label,weather,fraction,fence):
                        if list(job.glob('attempt_*/failure.json')):
                            raise ValueError('Previous attempt stopped on evidence; no automatic retry: '+name)
                        matrix.ROOT=ROOT
                        while True:
                            resources=matrix.resource_check()
                            recovery_finished=national_recovery_finished()
                            if resources['ready'] and recovery_finished:
                                break
                            publish('waiting_for_resources',current=name,resources=resources,
                                    national_recovery_finished=recovery_finished,worker=None)
                            time.sleep(30)
                        number=len(list(job.glob('attempt_*')))+1
                        job=job/f'attempt_{number:02d}'
                        job.mkdir(parents=True,exist_ok=False)
                        env={**os.environ,'TMP':str(job),'TEMP':str(job),'MPLCONFIGDIR':str(job/'mpl')}
                        with (job/'worker.log').open('xb') as log:
                            child=subprocess.Popen([sys.executable,'-B','-u',str(SCRIPT),'worker',label,
                                str(weather or 0),str(fraction),str(job)],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                                stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
                            publish('running',current=name,worker=dict(pid=child.pid,created=psutil.Process(child.pid).create_time()))
                            rc=child.wait()
                        if rc and not receipt(ROOT,label,weather,fraction,fence):
                            raise ValueError('Worker did not pass: '+str(job))
                data,marker=receipt(ROOT,label,weather,fraction,fence)
                state['completed'].append(dict(job=name,status=data['status'],marker_sha256=digest(marker)))
                publish('accepted_job',current=name,worker=None)
            save(ROOT/'results.json',dict(diagnostic_sha256=fence,cases=state['completed']))
            publish('completed_verified',current=None,results_sha256=digest(ROOT/'results.json'))
        except BaseException:
            publish('stopped',error=traceback.format_exc())
            raise


if __name__=='__main__':
    require_owned()
    if sys.argv[1:] == ['verify']:
        validate_staged()
    elif sys.argv[1:] == ['controller']:
        run()
    elif len(sys.argv)==6 and sys.argv[1]=='worker':
        target=Path(sys.argv[5]).resolve()
        if ROOT/'jobs' not in target.parents:
            raise ValueError('Worker target outside owned jobs')
        try:
            worker(sys.argv[2],int(sys.argv[3]) or None,float(sys.argv[4]),target)
        except BaseException:
            save(target/'failure.json',dict(error=traceback.format_exc(),utc=batch.utc()))
            raise
    else:
        raise ValueError('Unexpected controller or worker command')

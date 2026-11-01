"""Choose independent-channel rollout settings on validation years only."""
from pathlib import Path
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[key]='2'
os.environ['MPLBACKEND']='Agg'
import hashlib
import importlib.util
import json
import sys
import time
import traceback

HERE=Path(__file__).resolve().parent
BASE=Path('E:/Roko/remote_access/energy_rebuild_20261019')
sys.path[:0]=[str(BASE/'drift_audit/vendor'),str(BASE/'runtime_packages')]
os.environ['MPLCONFIGDIR']=str(HERE/'mpl_cache')
import numpy as np
import pandas as pd
from fit import save,digest


def load_engine():
    p=json.loads((HERE/'protocol.json').read_text())
    spec=importlib.util.spec_from_file_location('repaired_climate_engine',HERE/'engine.py')
    g=importlib.util.module_from_spec(spec);sys.modules[spec.name]=g;spec.loader.exec_module(g)
    g.CONFIG.latitude=p['latitude'];g.CONFIG.longitude=p['longitude']
    g.CONFIG.solar_sites=tuple(tuple(x) for x in p['solar_sites'])
    models,params=g.load_models(str(HERE/'fit_01/models'))
    return p,g,models,params


def generate_candidates(g,models,params,training,channel,options,count,seed,progress=None):
    """Paired candidates share initial calendar-aligned history and innovations."""
    ci=0 if channel=='Solar' else 2
    rng=np.random.RandomState((int(seed)+104729*(ci+1)) % 2**32)
    annual=training.reshape(-1,8760,3)
    initial=annual[rng.randint(len(annual),size=count),-170:,ci]
    rho=float(params[channel.lower()+'_rho'])
    eps=rng.randn(8760,count).astype(np.float32)
    noise=np.empty_like(eps);noise[0]=eps[0]
    for t in range(1,8760):noise[t]=rho*noise[t-1]+np.sqrt(1-rho*rho)*eps[t]
    history=np.tile(initial,(len(options),1)).astype(np.float32)
    result=np.empty((len(options)*count,8760),np.float32)
    alpha=np.repeat([v['drift_alpha'] for v in options],count).astype(np.float32)
    sigma=np.repeat([v['noise_multiplier'] for v in options],count).astype(np.float32)*float(params[channel.lower()+'_sigma_cal'])
    anchor=params['hourly_mean'][:,ci]
    fe=g.ChannelFeatureEngineer(channel,anchor_profile=anchor,latitude=g.CONFIG.latitude)
    boosters=[]
    for q in (.16,.5,.84):
        estimator=models[channel][q]
        booster=estimator.get_booster()
        booster.set_param({'device':'cpu','nthread':2})
        stop=int(estimator.best_iteration)+1
        boosters.append((booster,stop))
    hours=np.arange(8760)
    support=g.solar_interval_elevation(hours//24+1,hours%24)>-1
    floor=float(params['temp_train_min'])-10;ceiling=float(params['temp_train_max'])+10
    for t in range(8760):
        if channel=='Solar' and not support[t]:
            values=np.zeros(len(history),np.float32)
        else:
            X=fe.get_batch_feature_vector(t,history)
            low,mid,high=g.ordered_quantiles(*[b.inplace_predict(X,iteration_range=(0,stop)) for b,stop in boosters])
            values=mid+(high-low)/2*sigma*np.tile(noise[t],len(options))
            if channel!='Solar' or anchor[t]>.01:values=(1-alpha)*values+alpha*anchor[t]
            values=np.clip(values,0,1) if channel=='Solar' else np.clip(values,floor,ceiling)
        result[:,t]=values
        history[:,:-1]=history[:,1:];history[:,-1]=values
        if progress and (t+1)%2190==0:progress(t+1)
    return result.reshape(len(options),count,8760)


def fit_bias(values,training,ci,target):
    target=float(target)
    if ci==2:
        bounds=[float(training[:,:,ci].min())-10,float(training[:,:,ci].max())+10]
        lo,hi=-100.,100.
        for _ in range(50):
            mid=(lo+hi)/2
            if np.clip(values+mid,*bounds).mean()<target:lo=mid
            else:hi=mid
        return {'kind':'offset','value':(lo+hi)/2,'bounds':bounds}
    lo,hi=.001,100.
    if not np.clip(values*hi,0,1).mean()>=target:raise ValueError('Solar mean cannot be calibrated from these paths')
    for _ in range(50):
        mid=(lo+hi)/2
        if np.clip(values*mid,0,1).mean()<target:lo=mid
        else:hi=mid
    return {'kind':'scale','value':(lo+hi)/2}


def apply_bias(values,bias):
    if bias['kind']=='offset':return np.clip(values+bias['value'],*bias['bounds'])
    return np.clip(values*bias['value'],0,1)


def diagnostics(values,training,validation,ci):
    mask=(training[:,:,0].max(axis=0)>.005) if ci==0 else np.ones(8760,bool)
    truth=validation[:,:,ci]
    ratios={}
    def ratio(a,b):return float(a/max(float(b),1e-10))
    ratios['calendar_sd_ratio']=ratio(values.std(axis=0)[mask].mean(),training[:,:,ci].std(axis=0)[mask].mean())
    ratios['ramp95_ratio']=ratio(np.quantile(np.abs(np.diff(values,axis=1)),.95),np.quantile(np.abs(np.diff(truth,axis=1)),.95))
    ratios['annual_sd_ratio']=ratio(values.mean(axis=1).std(),training[:,:,ci].mean(axis=1).std())
    records={**ratios,'mean_bias':float(values.mean()-truth.mean())}
    for level in (.8,.95):
        low,high=np.quantile(values,[(1-level)/2,1-(1-level)/2],axis=0)
        records['coverage'+str(round(100*level))]=float(((truth>=low)&(truth<=high))[:,mask].mean())
        records['width'+str(round(100*level))]=float((high-low)[mask].mean())
    records['acf24']=float(np.mean([np.corrcoef(x[:-24],x[24:])[0,1] for x in values]))
    refacf=float(np.mean([np.corrcoef(x[:-24],x[24:])[0,1] for x in truth]))
    records['acf24_abs_error']=abs(records['acf24']-refacf)
    # Annual variation is reported separately; the same mean fit should not hide it.
    records['score']=float(np.mean([abs(np.log(np.clip(v,.02,50))) for v in ratios.values()])
        +4*abs(records['coverage95']-.95)+4*abs(records['coverage80']-.8)
        +abs(records['mean_bias'])/(.1 if ci==0 else 3.5)+records['acf24_abs_error'])
    return records


def accepted(record,protocol,ci):
    limits=protocol['validation_acceptance']
    checks={key:limits[key+'_min']<=record[key]<=limits[key+'_max']
            for key in ('coverage95','coverage80','ramp95_ratio','calendar_sd_ratio','annual_sd_ratio')}
    checks['mean_bias']=abs(record['mean_bias'])<=limits['mean_solar_bias_CF_max' if ci==0 else 'mean_temperature_bias_C_max']
    return all(checks.values()),checks


def main():
    started=time.monotonic()
    out=HERE/'rollout_04';out.mkdir(exist_ok=False)
    status={'status':'running','phase':'waiting_for_fit'}
    save(out/'status.json',status)
    try:
        while True:
            path=HERE/'fit_01/status.json'
            if path.exists():
                st=json.loads(path.read_text())
                if st['status']=='failed':raise RuntimeError('Fitting failed')
                if st['status']=='finished':break
            time.sleep(10)
        protocol,g,models,params=load_engine()
        freeze=json.loads((HERE/'fit_01/model_freeze.json').read_text())
        assert freeze['engine_sha256']==digest(HERE/'engine.py')
        frame=pd.read_parquet(BASE/'artifacts/HR_data.parquet')
        dates=pd.to_datetime(frame.time)
        selected=frame.loc[(dates.dt.year<=2019)&~((dates.dt.month==2)&(dates.dt.day==29)),['solar','wind','temperature']].to_numpy(float)
        del frame
        training=selected[:36*8760].reshape(36,8760,3)
        validation=selected[36*8760:].reshape(4,8760,3)
        options=[{'drift_alpha':a,'noise_multiplier':s} for a in protocol['candidate_drift_alpha'] for s in protocol['candidate_noise_multiplier']]
        choices={};all_rows=[]
        for channel,ci in [('Solar',0),('Temperature',2)]:
            status.update(phase='screening',channel=channel,generated_hour=0);save(out/'status.json',status)
            def progress(t):
                status.update(generated_hour=t);save(out/'status.json',status)
            values=generate_candidates(g,models,params,training,channel,options,
                protocol['screen_members_per_candidate'],protocol['calibration_seed'],progress)
            screen=[]
            for i,option in enumerate(options):
                bias=fit_bias(values[i],training,ci,float(validation[:,:,ci].mean()))
                record={'channel':channel,**option,'bias':bias,
                        **diagnostics(apply_bias(values[i],bias),training,validation,ci)}
                screen.append(record)
            screen.sort(key=lambda x:x['score'])
            save(out/(channel.lower()+'_screen.json'),screen)
            finalists=screen[:3]
            status.update(phase='confirming',channel=channel,generated_hour=0);save(out/'status.json',status)
            confirmation=generate_candidates(g,models,params,training,channel,finalists,
                protocol['confirmation_members'],protocol['confirmation_seed'],progress)
            rows=[]
            for i,option in enumerate(finalists):
                record={'channel':channel,'drift_alpha':option['drift_alpha'],'noise_multiplier':option['noise_multiplier'],
                        'bias':option['bias'],**diagnostics(apply_bias(confirmation[i],option['bias']),training,validation,ci)}
                okay,checks=accepted(record,protocol,ci)
                record.update(accepted=okay,acceptance_checks=checks);rows.append(record)
            rows.sort(key=lambda x:x['score'])
            save(out/(channel.lower()+'_confirmation.json'),rows)
            all_rows.extend(rows)
            eligible=[r for r in rows if r['accepted']]
            if not eligible:
                status.update(status='validation_not_accepted',phase='needs_design_review',channel=channel,
                              message='Do not promote this generator or run downstream energy studies.')
                save(out/'status.json',status)
                return
            choices[channel]=eligible[0]
        record={'selected':choices,'protocol_sha256':digest(HERE/'protocol.json'),
                'engine_sha256':digest(HERE/'engine.py'),'fitting_freeze_sha256':digest(HERE/'fit_01/model_freeze.json'),
                'calibration_source_sha256':digest(__file__),'selection_data_years':[2016,2019],
                'reporting_data_used_for_selection':False,'reporting_previously_inspected':True}
        save(out/'calibration_freeze.json',record)
        status.update(status='finished',phase='calibration_frozen',seconds=time.monotonic()-started)
    except Exception:
        status.update(status='failed',error=traceback.format_exc());raise
    finally:save(out/'status.json',status)


if __name__=='__main__':main()

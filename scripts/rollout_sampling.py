"""Independent-channel annual rollout sampling and validation calibration."""

import numpy as np


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


def generate_profiles(engine, models, parameters, training, settings, count, seed):
    """Generate independent channels using frozen rollout and bias calibration."""
    if training is None:
        raise ValueError('Training history is required for calendar-aligned initialization')
    if set(settings) != {'Solar','Temperature'}:
        raise ValueError('Both channels require frozen rollout calibration')
    result=np.empty((count,8760,3),np.float32)
    for channel,ci in [('Solar',0),('Temperature',2)]:
        option=settings[channel]
        paths=generate_candidates(engine,models,parameters,training,channel,[option],count,seed)[0]
        result[:,:,ci]=apply_bias(paths,option['bias']).astype(np.float32)
    pool=parameters.get('wind_train_data')
    if pool is None:raise ValueError('Training wind pool is required')
    result[:,:,1]=engine._generate_wind_bootstrap(pool,8760,count,seed=seed)
    return result


def fit_settings(engine, models, parameters, training, validation, protocol, progress=None):
    """Screen paired rollouts and confirm finalists on independent random draws."""
    train=np.asarray(training).reshape(-1,8760,3)
    val=np.asarray(validation).reshape(-1,8760,3)
    options=[{'drift_alpha':a,'noise_multiplier':s}
             for a in protocol['candidate_drift_alpha'] for s in protocol['candidate_noise_multiplier']]
    selected={};records={}
    for channel,ci in [('Solar',0),('Temperature',2)]:
        if progress:progress(channel,'screening')
        paths=generate_candidates(engine,models,parameters,train,channel,options,
            protocol['screen_members_per_candidate'],protocol['calibration_seed'])
        screen=[]
        for i,option in enumerate(options):
            bias=fit_bias(paths[i],train,ci,float(val[:,:,ci].mean()))
            screen.append({**option,'bias':bias,
                **diagnostics(apply_bias(paths[i],bias),train,val,ci)})
        screen.sort(key=lambda record:record['score'])
        finalists=screen[:3]
        if progress:progress(channel,'confirmation')
        paths=generate_candidates(engine,models,parameters,train,channel,finalists,
            protocol['confirmation_members'],protocol['confirmation_seed'])
        confirmation=[]
        for i,option in enumerate(finalists):
            record={key:option[key] for key in ('drift_alpha','noise_multiplier','bias')}
            record.update(diagnostics(apply_bias(paths[i],option['bias']),train,val,ci))
            okay,checks=accepted(record,protocol,ci)
            record.update(accepted=okay,acceptance_checks=checks)
            confirmation.append(record)
        confirmation.sort(key=lambda record:record['score'])
        records[channel]={'screen':screen,'confirmation':confirmation}
        eligible=[record for record in confirmation if record['accepted']]
        if not eligible:
            raise ValueError(channel+' rollout calibration did not satisfy the declared validation checks')
        selected[channel]=eligible[0]
    return selected,records

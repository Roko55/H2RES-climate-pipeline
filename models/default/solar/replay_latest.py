"""Reproduce the frozen solar. Run with Python and NumPy/SciPy/XGBoost2.1.4.
No historical or reporting observations are loaded. Optional --output PATH
saves the verified array. The default verifies without writing an output.
"""
from pathlib import Path
from types import SimpleNamespace
import json,hashlib,sys,io
import numpy as np,xgboost as xgb
from scipy.special import ndtr
import feature_runtime as features
ROOT=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def phase(*args,**kwargs):pass
class TrainingMoments:
 def mean(self,axis):
  if axis==0:return INPUTS['hourly_mean']
  if axis==1:return INPUTS['annual_means']
  raise ValueError(axis)
def annualize(z, seed):
    rng = np.random.RandomState(seed + 104759)
    targets = H.mean(1)[rng.randint(36, size=len(z))]
    return np.clip(z * (targets / np.maximum(z.mean(1), 1e-10))[:, None], 0, 1).astype(np.float32)

def generate_family(family, options, count, seed):
    ctx = np.load(family['envelope_path'])
    env = ctx['envelope']
    anchor = ctx['anchor']
    cap = float(ctx['cap'])
    fe = ENGINE.ChannelFeatureEngineer('Solar', anchor_profile=anchor, latitude=ENGINE.CONFIG.latitude)
    entries = sorted(family['models'], key=lambda item: item['q'])
    quantiles = np.asarray([e['q'] for e in entries])
    boosters = []
    for entry in entries:
        assert sha(entry['path']) == entry['sha256']
        booster = xgb.Booster(params={'nthread': 2})
        booster.load_model(entry['path'])
        boosters.append(booster)
    rng = np.random.RandomState(seed + 104729)
    n = len(options)
    total = n * count
    history = np.tile(ctx['initial_history'][rng.randint(36, size=count)], (n, 1)).astype(np.float32)
    eps = rng.randn(8760, count).astype(np.float32)
    state = np.tile(eps[0], n)
    rho = np.repeat([o['rho'] for o in options], count)
    noise = np.repeat([o['noise'] for o in options], count)
    alpha = np.repeat([o['alpha'] for o in options], count)
    out = np.empty((total, 8760), np.float32)
    dense = len(entries) > 3
    raw_anchor = H.mean(0)
    for t in range(8760):
        if t:
            state = rho * state + np.sqrt(1 - rho * rho) * np.tile(eps[t], n)
        if not SUPPORT[t]:
            values = np.zeros(total)
        else:
            X = fe.get_batch_feature_vector(t, history)
            pred = np.sort(np.stack([b.inplace_predict(X) for b in boosters]), axis=0)
            if dense:
                probabilities = ndtr(state * noise)
                j = np.sum(probabilities[None, :] > quantiles[:, None], axis=0) - 1
                j = np.clip(j, 0, len(quantiles) - 2)
                col = np.arange(total)
                values = pred[j, col] + (probabilities - quantiles[j]) / (quantiles[j + 1] - quantiles[j]) * (pred[j + 1, col] - pred[j, col])
            else:
                values = pred[1] + (pred[2] - pred[0]) / 2 * state * noise
            if raw_anchor[t] > 0.01:
                values = (1 - alpha) * values + alpha * anchor[t]
            values = np.clip(values, 0, cap)
            raw = np.clip(values * env[t], 0, 1)
            values = raw / env[t]
        raw = np.clip(values * env[t], 0, 1)
        out[:, t] = raw
        history[:, :-1] = history[:, 1:]
        history[:, -1] = values
        if (t + 1) % 2190 == 0:
            phase('generating_paths', family=family['id'], hour=t + 1, options=n, members=count, seed=seed)
    return {o['id']: annualize(z, seed) for o, z in zip(options, out.reshape(n, count, 8760))}

def process(raw, coeff, post, seed):
    if post == 'raw':
        return raw.copy()
    z = coeff['center'][None, :] + 0.5 * (coeff['target'] - coeff['center'])[None, :] + (raw - coeff['center'][None, :]) * (0.5 + 0.5 * coeff['amplitude'])[None, :]
    z = np.clip(z, 0, 1)
    z[:, ~SUPPORT] = 0
    z = annualize(z, seed)
    if post == 'seasonal_partial_central_blend':
        z = np.clip(z + 0.5 * (OLD_MEAN - coeff['processed_mean'])[None, :], 0, 1)
    z[:, ~SUPPORT] = 0
    return z.astype(np.float32)
def main():
 global INPUTS,H,SUPPORT,MONTH,OLD_MEAN,ENGINE
 recipe=json.loads((ROOT/'runtime_recipe.json').read_text())
 assert xgb.__version__==recipe['xgboost_version'], 'Use the recorded XGBoost version for exact replay'
 for name,digest in recipe['pinned_files'].items():assert sha(ROOT/name)==digest,name
 INPUTS=np.load(ROOT/'runtime_inputs.npz');H=TrainingMoments();SUPPORT=INPUTS['support'];MONTH=INPUTS['month'];OLD_MEAN=INPUTS['old_mean'];ENGINE=features
 family=recipe['family'];family['envelope_path']=str(ROOT/family['envelope_path'])
 for entry in family['models']:entry['path']=str(ROOT/entry['path'])
 option=recipe['option'];raw=generate_family(family,[option['rollout']],recipe['count'],recipe['seed'])[option['rollout']['id']]
 coeff=dict(np.load(ROOT/recipe['coeff_file']));z=np.clip(process(raw,coeff,option['post'],recipe['seed'])*recipe['scale'],0,1).astype(np.float32)
 buf=io.BytesIO();np.save(buf,z);digest=hashlib.sha256(buf.getvalue()).hexdigest();assert digest==recipe['expected_solar_sha256'],digest
 if '--output' in sys.argv:Path(sys.argv[sys.argv.index('--output')+1]).write_bytes(buf.getvalue())
 print(json.dumps(dict(exact_replay=True,solar_sha256=digest,shape=list(z.shape),raw_observations_loaded=False)))
if __name__=='__main__':main()

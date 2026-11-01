"""Replay the accepted cohort and generate draws from its frozen models.

Solar executes only the hash-checked numerical functions of the delivered replay
source. Temperature and wind use the executed independent-channel functions.
The accepted wind paths remain available without redistributing the raw pool.
"""
from pathlib import Path
from types import SimpleNamespace
import ast
import hashlib
import io
import json
import numpy as np
import xgboost as xgb
from scipy.special import ndtr
from . import channel_runtime

ENSEMBLE_SHA = 'd86a855166c24d699d511010184db12d0baf1551f83254b41f661c602d7ae728'
SOLAR_SHA = '4e87e12baa22dede50d905392ee02467fcf27b83e1483b8b8c4c9d9365bd6b3e'
REPLAY_SHA = '175fc5948fbe711262104e97f304e3557e72f1db3b75af77b318c79a80a81004'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def array_digest(array):
    stream = io.BytesIO()
    np.save(stream, array)
    return hashlib.sha256(stream.getvalue()).hexdigest()


def checked(root, relative, expected):
    path = (root/relative).resolve()
    path.relative_to(root.resolve())
    if digest(path) != expected:
        raise ValueError('Frozen model/input hash differs: '+relative)
    return path


def solar_context(folder):
    checked(folder, 'runtime_recipe.json', '46dc6bf977964ae48a13cbbf1633e2510b3d44536c106f5ba179277e29fe2068')
    recipe = json.loads((folder/'runtime_recipe.json').read_text())
    if xgb.__version__ != recipe['xgboost_version']:
        raise ValueError('Exact solar replay requires XGBoost '+recipe['xgboost_version'])
    for name, expected in recipe['pinned_files'].items():
        checked(folder, name, expected)
    checked(folder, 'replay_latest.py', REPLAY_SHA)
    # Import no controller, filesystem entry point or model-selection code.
    features = {'np': np, '__name__': 'frozen_solar_features'}
    exec(compile((folder/'feature_runtime.py').read_text(), str(folder/'feature_runtime.py'), 'exec'), features)
    inputs = dict(np.load(folder/'runtime_inputs.npz', allow_pickle=False))

    def mean(axis):
        if axis == 0:
            return inputs['hourly_mean']
        if axis == 1:
            return inputs['annual_means']
        raise ValueError(axis)

    scope = {'np': np, 'xgb': xgb, 'ndtr': ndtr, 'sha': digest,
             'phase': lambda *a, **kw: None, 'H': SimpleNamespace(mean=mean),
             'SUPPORT': inputs['support'], 'OLD_MEAN': inputs['old_mean'],
             'ENGINE': SimpleNamespace(**{k: v for k, v in features.items() if not k.startswith('__')})}
    tree = ast.parse((folder/'replay_latest.py').read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in {'annualize', 'generate_family', 'process'}]
    if len(nodes) != 3:
        raise ValueError('Incomplete frozen solar implementation')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'frozen_solar_numerics', 'exec'), scope)
    return recipe, scope


def generate_solar(root, count=100, seed=42):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 128:
        raise ValueError('Count must be an integer between 1 and 128')
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32-104759:
        raise ValueError('Seed lies outside the supported RandomState range')
    folder = root/'models/default/solar'
    recipe, scope = solar_context(folder)
    family = recipe['family']
    family['envelope_path'] = str(folder/family['envelope_path'])
    for entry in family['models']:
        entry['path'] = str(folder/entry['path'])
    option = recipe['option']
    raw = scope['generate_family'](family, [option['rollout']], count, seed)[option['rollout']['id']]
    coefficients = dict(np.load(folder/recipe['coeff_file'], allow_pickle=False))
    values = np.clip(scope['process'](raw, coefficients, option['post'], seed)*recipe['scale'], 0, 1).astype(np.float32)
    if values.shape != (count, 8760) or not np.isfinite(values).all():
        raise ValueError('Invalid solar profiles')
    if np.any(values[:, ~scope['SUPPORT']] != 0):
        raise ValueError('Solar outside the frozen astronomical support')
    if count == 100 and seed == 42 and array_digest(values) != SOLAR_SHA:
        raise ValueError('Solar replay differs from the accepted production array')
    return values


def accepted_array(root):
    path = checked(root, 'profiles/accepted.npy', ENSEMBLE_SHA)
    values = np.load(path, allow_pickle=False)
    if values.shape != (100, 8760, 3) or not np.isfinite(values).all():
        raise ValueError('Invalid accepted cohort')
    if ((values[:, :, :2] < 0) | (values[:, :, :2] > 1)).any():
        raise ValueError('Accepted capacity-factor bounds violated')
    if array_digest(values[:, :, 0]) != SOLAR_SHA:
        raise ValueError('Accepted solar channel identity differs')
    return values


def replay(root, regenerate_solar=True):
    values = accepted_array(root)
    if regenerate_solar:
        values[:, :, 0] = generate_solar(root)
    if array_digest(values) != ENSEMBLE_SHA:
        raise ValueError('Full accepted cohort replay differs')
    return values


def generate_with_data(root, raw, count, seed):
    """Raw input is already validated and bound to the final corpus SHA upstream."""
    metadata = json.loads((root/'models/default/metadata.json').read_text())
    training = raw[:36*8760]
    if training.shape != (36*8760, 3):
        raise ValueError('The released models require 36 complete training years')
    annual = training.reshape(36, 8760, 3)
    temperature = metadata['temperature']
    params = {'hourly_mean': annual.mean(0), 'hourly_std': annual.std(0),
              'temp_train_min': float(training[:, 2].min()), 'temp_train_max': float(training[:, 2].max()),
              'temperature_rho': temperature['rho'], 'temperature_sigma_cal': temperature['sigma_calibration']}
    models = {'Temperature': {}}
    for entry in metadata['models']:
        if entry['channel'] != 'Temperature':
            continue
        path = checked(root/'models/default', entry['file'], entry['sha256'])
        model = xgb.XGBRegressor()
        model.load_model(path)
        models['Temperature'][float(entry['quantile'])] = model
    _, scope = solar_context(root/'models/default/solar')
    engine = scope['ENGINE']
    engine.ordered_quantiles = channel_runtime.ordered_quantiles
    options = [{'drift_alpha': temperature['drift_alpha'], 'noise_multiplier': temperature['noise_multiplier']}]
    temp = channel_runtime.generate_candidates(engine, models, params, training, 'Temperature', options, count, seed)[0]
    temp = channel_runtime.apply_bias(temp, temperature['bias'])
    wind = channel_runtime._generate_wind_bootstrap(training[:, 1], 8760, count, seed)
    solar = generate_solar(root, count, seed)
    result = np.stack([solar, wind, temp], axis=-1).astype(np.float32)
    if count == 100 and seed == 42 and array_digest(result) != ENSEMBLE_SHA:
        raise ValueError('Full model regeneration differs from the frozen cohort')
    return result

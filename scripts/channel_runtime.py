"""Executed independent temperature rollout and seasonal wind sampling."""
import numpy as np
from types import SimpleNamespace
CONFIG=SimpleNamespace(latitude=44.52)
def ordered_quantiles(q16, q50, q84):
    """Use the same monotone rearrangement during calibration and sampling."""
    return np.sort(np.stack((q16, q50, q84), axis=0), axis=0)

def _generate_wind_bootstrap(wind_train: np.ndarray, n_hours: int, n_scenarios: int, seed: int=None) -> np.ndarray:
    """
    Generate synthetic wind capacity-factor profiles via season-aware block
    bootstrap.

    Contiguous blocks of 144 hours (~6 days, matching synoptic weather-system
    duration) are sampled from the historical training data, restricted to the
    same meteorological season.  Adjacent blocks are cross-faded over 4 hours
    to avoid discontinuities.  This preserves the observed temporal structure
    (storm persistence, calm spells) without requiring a parametric model.

    Returns shape ``(n_scenarios, n_hours)``.
    """
    rng = np.random.RandomState(seed)
    block_len = 144
    crossfade = 4
    pool_len = len(wind_train)
    pool_hoy = np.arange(pool_len) % 8760
    _hpm = [d * 24 for d in [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]]
    _cum = np.cumsum([0] + _hpm)
    pool_months = np.zeros(pool_len, dtype=np.int32)
    for m in range(12):
        mask = (pool_hoy >= _cum[m]) & (pool_hoy < _cum[m + 1])
        pool_months[mask] = m
    if CONFIG.latitude >= 0:
        m2s = [0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 0]
    else:
        m2s = [2, 2, 3, 3, 3, 0, 0, 0, 1, 1, 1, 2]
    pool_seasons = np.array([m2s[m] for m in pool_months], dtype=np.int32)
    season_starts = {}
    season_names = {0: 'Winter (DJF)', 1: 'Spring (MAM)', 2: 'Summer (JJA)', 3: 'Autumn (SON)'}
    for sid in range(4):
        valid = np.where(pool_seasons == sid)[0]
        valid = valid[valid + block_len <= pool_len]
        if len(valid) == 0:
            print(f'  [WARN] Wind bootstrap: season {season_names[sid]} has no valid start positions (need at least {block_len} consecutive hours). Falling back to all-season pool — seasonal structure will be degraded.')
            valid = np.arange(max(1, pool_len - block_len))
        elif len(valid) < 10:
            print(f'  [WARN] Wind bootstrap: season {season_names[sid]} has only {len(valid)} valid start position(s). Bootstrap diversity will be limited.')
        season_starts[sid] = valid
    gen_hoy = np.arange(n_hours) % 8760
    gen_months = np.zeros(n_hours, dtype=np.int32)
    for m in range(12):
        mask = (gen_hoy >= _cum[m]) & (gen_hoy < _cum[m + 1])
        gen_months[mask] = m
    gen_seasons = np.array([m2s[m] for m in gen_months], dtype=np.int32)
    wind_scenarios = np.zeros((n_scenarios, n_hours), dtype=np.float32)
    for s in range(n_scenarios):
        t_pos = 0
        prev_tail = None
        while t_pos < n_hours:
            target_season = gen_seasons[t_pos]
            starts = season_starts[target_season]
            start = starts[rng.randint(len(starts))]
            block = wind_train[start:start + block_len].copy()
            end_pos = min(t_pos + block_len, n_hours)
            chunk = block[:end_pos - t_pos]
            if prev_tail is not None:
                fade_len = min(crossfade, len(chunk), len(prev_tail))
                alpha = np.linspace(0, 1, fade_len, dtype=np.float32)
                chunk[:fade_len] = (1 - alpha) * prev_tail[-fade_len:] + alpha * chunk[:fade_len]
            wind_scenarios[s, t_pos:end_pos] = chunk
            prev_tail = chunk
            t_pos = end_pos
    return np.clip(wind_scenarios, 0.0, 1.0)

def generate_candidates(g, models, params, training, channel, options, count, seed, progress=None):
    """Paired candidates share initial calendar-aligned history and innovations."""
    ci = 0 if channel == 'Solar' else 2
    rng = np.random.RandomState((int(seed) + 104729 * (ci + 1)) % 2 ** 32)
    annual = training.reshape(-1, 8760, 3)
    initial = annual[rng.randint(len(annual), size=count), -170:, ci]
    rho = float(params[channel.lower() + '_rho'])
    eps = rng.randn(8760, count).astype(np.float32)
    noise = np.empty_like(eps)
    noise[0] = eps[0]
    for t in range(1, 8760):
        noise[t] = rho * noise[t - 1] + np.sqrt(1 - rho * rho) * eps[t]
    history = np.tile(initial, (len(options), 1)).astype(np.float32)
    result = np.empty((len(options) * count, 8760), np.float32)
    alpha = np.repeat([v['drift_alpha'] for v in options], count).astype(np.float32)
    sigma = np.repeat([v['noise_multiplier'] for v in options], count).astype(np.float32) * float(params[channel.lower() + '_sigma_cal'])
    anchor = params['hourly_mean'][:, ci]
    fe = g.ChannelFeatureEngineer(channel, anchor_profile=anchor, latitude=g.CONFIG.latitude)
    boosters = []
    for q in (0.16, 0.5, 0.84):
        estimator = models[channel][q]
        booster = estimator.get_booster()
        booster.set_param({'device': 'cpu', 'nthread': 2})
        stop = int(estimator.best_iteration) + 1
        boosters.append((booster, stop))
    hours = np.arange(8760)
    support = g.solar_interval_elevation(hours // 24 + 1, hours % 24) > -1
    floor = float(params['temp_train_min']) - 10
    ceiling = float(params['temp_train_max']) + 10
    for t in range(8760):
        if channel == 'Solar' and (not support[t]):
            values = np.zeros(len(history), np.float32)
        else:
            X = fe.get_batch_feature_vector(t, history)
            low, mid, high = g.ordered_quantiles(*[b.inplace_predict(X, iteration_range=(0, stop)) for b, stop in boosters])
            values = mid + (high - low) / 2 * sigma * np.tile(noise[t], len(options))
            if channel != 'Solar' or anchor[t] > 0.01:
                values = (1 - alpha) * values + alpha * anchor[t]
            values = np.clip(values, 0, 1) if channel == 'Solar' else np.clip(values, floor, ceiling)
        result[:, t] = values
        history[:, :-1] = history[:, 1:]
        history[:, -1] = values
        if progress and (t + 1) % 2190 == 0:
            progress(t + 1)
    return result.reshape(len(options), count, 8760)

def apply_bias(values, bias):
    if bias['kind'] == 'offset':
        return np.clip(values + bias['value'], *bias['bounds'])
    return np.clip(values * bias['value'], 0, 1)

# -*- coding: utf-8 -*-
"""
XGBoost Climate Scenario Generator for H2RES.

Generates synthetic annual climate profiles (solar capacity factor, wind
capacity factor, ambient temperature) for energy system optimisation under
uncertainty.  Each climate channel is modelled independently — the underlying
data sources correspond to different geographic locations and physical
processes, so no cross-channel features are used.

Modelling approach
------------------
Solar / Temperature
    XGBoost quantile regression (q16, q50, q84) trained on per-channel
    time-series features (lags, rolling means, calendar encodings, solar
    elevation).  Generation is autoregressive: at each hour the model
    predicts the conditional distribution, and a sample is drawn using
    AR(1)-correlated noise whose variance is calibrated on the validation
    set (data-driven ``sigma_calibration`` and ``rho``).  A small drift-
    correction term anchors the long-run mean to the historical climatology,
    preventing compounding bias over 8 760 autoregressive steps.

Wind
    Season-aware block bootstrap of historical capacity factors.  Blocks
    of 144 hours (~6 days, matching synoptic weather-system duration) are
    resampled from the same season and cross-faded at boundaries.  This
    preserves observed temporal structure (storm persistence, calm spells)
    without requiring a generative model.

Usage examples
--------------
    python xgboost_climate_model.py --train --compare
    python xgboost_climate_model.py --generate --num_scenarios 100
    python xgboost_climate_model.py --validate
    python xgboost_climate_model.py --train --country DE --latitude 51.0
    python xgboost_climate_model.py --train --seed 123
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from typing import Dict, List, Tuple
import warnings

from scipy.stats import ks_2samp, wasserstein_distance
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION
# =============================================================================
from dataclasses import dataclass
import json

COUNTRY = "HR"

COUNTRY_REGISTRY = {
    "HR": (44.52, "Croatia"),
    "NO": (60.0, "Norway"),
    "ES": (40.0, "Spain"),
    "DE": (51.0, "Germany"),
    "IT": (42.0, "Italy"),
    "FR": (46.0, "France"),
    "GR": (38.0, "Greece"),
    "PT": (39.5, "Portugal"),
    "SE": (62.0, "Sweden"),
    "DK": (56.0, "Denmark"),
    "IE": (53.0, "Ireland"),
    "PL": (52.0, "Poland"),
    "AT": (47.5, "Austria"),
    "FI": (64.0, "Finland"),
    "NL": (52.5, "Netherlands"),
    "BE": (50.5, "Belgium"),
    "CZ": (50.0, "Czech Republic"),
    "RO": (46.0, "Romania"),
    "BG": (43.0, "Bulgaria"),
    "HU": (47.0, "Hungary"),
}

# Channels that use XGBoost (Wind is excluded — uses bootstrap)
XGBOOST_CHANNELS = ["Solar", "Temperature"]
ALL_CHANNELS = ["Solar", "Wind", "Temperature"]
CHANNEL_IDX = {"Solar": 0, "Wind": 1, "Temperature": 2}


@dataclass
class ClimateConfig:
    hours_per_year: int = 8760
    latitude: float = 45.0
    country: str = "Croatia"
    data_path: str = ""
    models_dir: str = ""
    results_dir: str = ""
    is_colab: bool = False

    @classmethod
    def from_json(cls, json_path: str) -> 'ClimateConfig':
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cfg = cls()
        if 'latitude' in data:
            cfg.latitude = float(data['latitude'])
        if 'country' in data:
            cfg.country = data['country']
        print(f"[CONFIG] Loaded config for {cfg.country} (lat={cfg.latitude})")
        return cfg


def get_config(config_path: str = None, latitude: float = None) -> ClimateConfig:
    try:
        import google.colab
        is_colab = True
    except ImportError:
        is_colab = False

    if config_path and os.path.exists(config_path):
        cfg = ClimateConfig.from_json(config_path)
        cfg.is_colab = is_colab
    else:
        if COUNTRY not in COUNTRY_REGISTRY:
            print(f"  [WARN] Country '{COUNTRY}' not in registry. Using default lat 45.0.")
        lat, name = COUNTRY_REGISTRY.get(COUNTRY, (45.0, COUNTRY))
        cfg = ClimateConfig(is_colab=is_colab, latitude=lat, country=name)

    if latitude is not None:
        cfg.latitude = latitude

    if is_colab:
        cfg.data_path = f"/content/{COUNTRY}_data.parquet"
        cfg.models_dir = "/content/models"
        cfg.results_dir = "/content/results"
    else:
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            script_dir = os.getcwd()
        cfg.data_path = os.path.join(script_dir, "..", "data", "SC", f"{COUNTRY}_data.parquet")
        cfg.models_dir = os.path.join(script_dir, "..", "models")
        cfg.results_dir = os.path.join(script_dir, "..", "results")

    os.makedirs(cfg.models_dir, exist_ok=True)
    os.makedirs(cfg.results_dir, exist_ok=True)
    print(f"[CONFIG] Country: {cfg.country} ({COUNTRY}), Lat: {cfg.latitude}")
    return cfg


CONFIG = get_config()


def setup_environment(cfg: ClimateConfig):
    if not cfg.is_colab:
        return
    import subprocess
    for pkg in ["xgboost", "optuna", "joblib", "shap"]:
        try:
            __import__(pkg)
        except ImportError:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])


setup_environment(CONFIG)

HOURS_PER_YEAR = CONFIG.hours_per_year
RESULTS_DIR = CONFIG.results_dir
MODELS_DIR = CONFIG.models_dir

np.random.seed(42)

import xgboost as xgb
import joblib

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False


# =============================================================================
# DATA LOADING
# =============================================================================
def load_climate_data(parquet_path: str = None) -> np.ndarray:
    """
    Load and validate climate data from a Parquet file.

    Expects columns ``[solar, wind, temperature]`` plus an optional ``time``
    column.  Leap-day hours are removed and the series is aligned to start at
    January 1, 00:00.

    Returns
    -------
    np.ndarray
        Shape ``(N_hours, 3)`` with columns [solar, wind, temperature].
    """
    if parquet_path is None:
        parquet_path = CONFIG.data_path

    if not os.path.exists(parquet_path):
        raise FileNotFoundError(
            f"Data file not found: {parquet_path}\n"
            f"  Expected: {{COUNTRY}}_data.parquet with columns [solar, wind, temperature, time]"
        )

    print(f"Loading data from: {parquet_path}")
    df = pd.read_parquet(parquet_path)

    if 'time' in df.columns:
        df['time'] = pd.to_datetime(df['time'])
        is_leap_day = (df['time'].dt.month == 2) & (df['time'].dt.day == 29)
        if is_leap_day.any():
            print(f"  Removing {is_leap_day.sum()} leap day hours")
            df = df[~is_leap_day].copy()

    columns = ["solar", "wind", "temperature"]
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise KeyError(f"Missing columns: {missing}. Available: {list(df.columns)}")
    raw_data = df[columns].values.copy()

    # Alignment check
    if 'time' in df.columns:
        first_time = df['time'].iloc[0]
        offset = first_time.hour + (first_time.dayofyear - 1) * 24
        if offset != 0:
            print(f"  [WARN] Data doesn't start at Jan 1 00:00. Truncating to align.")
            raw_data = raw_data[8760 - offset:]

    print(f"  Loaded {len(raw_data)} hours ({len(raw_data)//8760} complete years).")
    return raw_data


def split_data(raw_data: np.ndarray, train_ratio: float = 0.8,
               val_ratio: float = 0.1) -> Dict:
    """
    Split climate data into train / validation / test sets by complete years.

    Also computes the hour-of-year climatology (mean and standard deviation
    over the training years), used as anchor profiles during generation.
    """
    num_years = len(raw_data) // HOURS_PER_YEAR
    years_data = raw_data[:num_years * HOURS_PER_YEAR].reshape(num_years, HOURS_PER_YEAR, -1)

    n_train = int(num_years * train_ratio)
    n_val = int(num_years * val_ratio)

    raw_train = years_data[:n_train].reshape(-1, 3)
    raw_val = years_data[n_train:n_train+n_val].reshape(-1, 3)
    raw_test = years_data[n_train+n_val:].reshape(-1, 3)

    train_3d = raw_train.reshape(-1, HOURS_PER_YEAR, 3)
    hourly_mean = train_3d.mean(axis=0)   # (8760, 3)
    hourly_std = train_3d.std(axis=0)     # (8760, 3)

    print(f"  Splits: Train={len(raw_train)} Val={len(raw_val)} Test={len(raw_test)}")
    return {
        'train': raw_train,
        'val': raw_val,
        'test': raw_test,
        'hourly_mean': hourly_mean,
        'hourly_std': hourly_std,
    }


# =============================================================================
# SOLAR PHYSICS
# =============================================================================
def calculate_solar_elevation(day_of_year, hour, lat=None):
    """Compute solar elevation angle in degrees (scalar or array inputs)."""
    if lat is None:
        lat = CONFIG.latitude
    declination = 23.44 * np.sin(np.radians(360 / 365 * (day_of_year - 81)))
    hour_angle = (hour - 12) * 15
    lat_rad = np.radians(lat)
    dec_rad = np.radians(declination)
    sha_rad = np.radians(hour_angle)
    sin_elev = (np.sin(lat_rad) * np.sin(dec_rad) +
                np.cos(lat_rad) * np.cos(dec_rad) * np.cos(sha_rad))
    return np.degrees(np.arcsin(np.clip(sin_elev, -1, 1)))


# =============================================================================
# FEATURE ENGINEERING (Per-Channel, Independent)
# =============================================================================
def _safe_shift(arr, num, fill_val=np.nan):
    result = np.empty_like(arr)
    if num > 0:
        result[:num] = fill_val
        result[num:] = arr[:-num]
    elif num < 0:
        result[num:] = fill_val
        result[:num] = arr[-num:]
    else:
        return arr
    return result


def _past_rolling(arr, window):
    """Mean of observations strictly before each target; prefix uses past only."""
    values = np.asarray(arr, dtype=float)
    cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
    end = np.arange(len(values))
    start = np.maximum(0, end - window)
    counts = end - start
    # No past exists at t=0; this row is discarded by the 168-hour warmup.
    return np.divide(cumulative[end] - cumulative[start], counts,
                     out=np.zeros(len(values), dtype=float), where=counts > 0)


class ChannelFeatureEngineer:
    """
    Per-channel feature engineering for XGBoost climate models.

    Each channel uses only its own history — no cross-channel features are
    created.  The feature vector for a single time step consists of:

    - 8 static calendar features (sin/cos of hour and day-of-year, solar
      elevation flag, hemisphere-aware summer flag, normalised hour/day)
    - 1 climatological anchor (hour-of-year mean from training data)
    - 3 autoregressive lags (1 h, 24 h, 168 h)
    - 2 rolling means (24 h, 168 h trailing windows)
    - 2 finite differences (1-step and 24-step)

    Total: 16 features per channel.
    """

    def __init__(self, channel: str, anchor_profile: np.ndarray = None,
                 latitude: float = None):
        """
        Parameters
        ----------
        channel : str
            ``"Solar"`` or ``"Temperature"``.
        anchor_profile : np.ndarray, optional
            Shape ``(8760,)`` climatological mean for this channel.
        latitude : float, optional
            Geographic latitude for solar-elevation calculation.
        """
        self.channel = channel
        self.ch_idx = CHANNEL_IDX[channel]
        self.latitude = latitude if latitude is not None else CONFIG.latitude
        self.lags = [1, 24, 168]
        self.rolling_windows = [24, 168]
        self.anchor_profile = anchor_profile  # (8760,) single channel

        # Build feature names
        self.feature_names = []
        ch_lower = channel.lower()[:4]  # "sola" or "temp"

        # Static features (8)
        self.feature_names += ['sin_hour', 'cos_hour', 'sin_day', 'cos_day',
                               'is_day', 'is_summer', 'hour_norm', 'day_norm']

        # Anchor (1)
        if self.anchor_profile is not None:
            self.feature_names.append(f'anchor_{ch_lower}')

        # Lags (3)
        for lag in self.lags:
            self.feature_names.append(f'lag_{lag}_{ch_lower}')

        # Rolling (2)
        for w in self.rolling_windows:
            self.feature_names.append(f'roll_{w}_{ch_lower}')

        # Diffs (2)
        self.feature_names += [f'diff1_{ch_lower}', f'diff24_{ch_lower}']

        self.feature_map = {n: i for i, n in enumerate(self.feature_names)}
        self.n_features = len(self.feature_names)

        # Pre-compute static features for 8760 hours
        self._static_8760 = self._make_static(8760)

    def _make_static(self, n):
        hours = np.arange(n) % 24
        days = (np.arange(n) // 24) % 365
        feats = [
            np.sin(2 * np.pi * hours / 24),
            np.cos(2 * np.pi * hours / 24),
            np.sin(2 * np.pi * days / 365),
            np.cos(2 * np.pi * days / 365),
            (calculate_solar_elevation(days + 1, hours.astype(float), self.latitude) > 0).astype(np.float32),
            ((days >= 91) & (days <= 273)).astype(np.float32) if self.latitude >= 0
            else ((days < 91) | (days > 273)).astype(np.float32),
            hours / 24.0,
            days / 365.0,
        ]
        return np.stack(feats, axis=1).astype(np.float32)  # (n, 8)

    def create_training_features(self, data: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Build feature matrix and target vector from raw climate data.

        The first 168 hours are discarded (warm-up period for lag features).

        Parameters
        ----------
        data : np.ndarray
            Shape ``(N_hours, 3)`` raw climate data.

        Returns
        -------
        X : np.ndarray
            Feature matrix, shape ``(N_hours - 168, n_features)``.
        Y : np.ndarray
            Target values for this channel, shape ``(N_hours - 168,)``.
        """
        n = len(data)
        values = data[:, self.ch_idx]
        Y = values.copy()

        # Static
        if n == 8760:
            static = self._static_8760
        else:
            static = self._make_static(n)

        feats = [static]

        # Anchor
        if self.anchor_profile is not None:
            n_rep = (n // 8760) + 1
            anchor = np.tile(self.anchor_profile, n_rep)[:n]
            feats.append(anchor.reshape(-1, 1))

        # Lags
        lag_feats = []
        for lag in self.lags:
            lag_feats.append(_safe_shift(values, lag, fill_val=np.mean(values)))
        feats.append(np.stack(lag_feats, axis=1))

        # Rolling
        roll_feats = []
        for w in self.rolling_windows:
            roll_feats.append(_past_rolling(values, w))
        feats.append(np.stack(roll_feats, axis=1))

        # Diffs
        l1 = _safe_shift(values, 1, fill_val=np.mean(values))
        l2 = _safe_shift(values, 2, fill_val=np.mean(values))
        l24 = _safe_shift(values, 24, fill_val=np.mean(values))
        feats.append(np.stack([l1 - l2, l1 - l24], axis=1))

        X = np.concatenate(feats, axis=1).astype(np.float32)

        # Skip first 168 hours (warm-up for lags)
        return X[168:], Y[168:].astype(np.float32)

    def get_batch_feature_vector(self, t: int, history_batch: np.ndarray) -> np.ndarray:
        """
        Construct the feature vector for all scenarios at generation step *t*.

        Parameters
        ----------
        t : int
            Hour index within the year (0–8759).
        history_batch : np.ndarray
            Shape ``(S, hist_len)`` — recent values for this channel across
            all *S* scenarios.

        Returns
        -------
        np.ndarray
            Shape ``(S, n_features)``.
        """
        S = history_batch.shape[0]
        hist_len = history_batch.shape[1]
        X = np.zeros((S, self.n_features), dtype=np.float32)

        hour_in_year = t % 8760
        X[:, :8] = self._static_8760[hour_in_year]

        idx = 8

        # Anchor
        if self.anchor_profile is not None:
            X[:, idx] = self.anchor_profile[hour_in_year]
            idx += 1

        # Lags
        for lag in self.lags:
            if hist_len >= lag:
                X[:, idx] = history_batch[:, -lag]
            idx += 1

        # Rolling
        for w in self.rolling_windows:
            if hist_len >= w:
                X[:, idx] = np.mean(history_batch[:, -w:], axis=1)
            idx += 1

        # Diffs
        if hist_len >= 2:
            X[:, idx] = history_batch[:, -1] - history_batch[:, -2]      # diff1
        idx += 1
        if hist_len >= 24:
            X[:, idx] = history_batch[:, -1] - history_batch[:, -24]     # diff24
        idx += 1

        return X


# =============================================================================
# MODEL CONFIGURATION & TRAINING
# =============================================================================
def get_default_params(use_gpu: bool = False) -> Dict:
    return {
        "tree_method": "hist",
        "device": "cuda" if use_gpu else "cpu",
        "random_state": 42,
        "verbosity": 0,
        "n_jobs": -1,
        "n_estimators": 1000,
        "max_depth": 6,
        "learning_rate": 0.10,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 10,
        "early_stopping_rounds": 50,
    }


def train_models(splits: Dict, use_gpu: bool = False,
                 custom_params: Dict[str, Dict] = None) -> Tuple[Dict, Dict]:
    """
    Train independent XGBoost quantile-regression models for Solar and
    Temperature.  Wind is handled by block bootstrap and does not require
    a trained model.

    Three quantile levels (0.16, 0.50, 0.84) are fitted per channel to
    characterise the conditional distribution at each hour.

    Returns
    -------
    models : dict
        ``{channel: {quantile: XGBRegressor}}``.
    feature_engineers : dict
        ``{channel: ChannelFeatureEngineer}``.
    """
    models = {}
    feature_engineers = {}
    QUANTILES = [0.16, 0.50, 0.84]

    print(f"\n[TRAINING] Independent channels (no chaining)")
    print(f"  XGBoost channels: {XGBOOST_CHANNELS}")
    print(f"  Bootstrap channels: Wind")

    hourly_mean = splits['hourly_mean']

    for channel in XGBOOST_CHANNELS:
        ch_idx = CHANNEL_IDX[channel]
        anchor = hourly_mean[:, ch_idx]

        fe = ChannelFeatureEngineer(channel, anchor_profile=anchor, latitude=CONFIG.latitude)
        feature_engineers[channel] = fe

        X_train, Y_train = fe.create_training_features(splits['train'])
        X_val, Y_val = fe.create_training_features(splits['val'])

        print(f"\n  Training {channel} ({fe.n_features} features)...", end=" ")

        tuned = {}
        if custom_params and channel in custom_params:
            tuned = custom_params[channel]
            print(f"[Tuned: d={tuned.get('max_depth')} lr={tuned.get('learning_rate', 0):.3f}]", end=" ")

        models[channel] = {}
        for q in QUANTILES:
            params = get_default_params(use_gpu)
            params.update(tuned)
            params['objective'] = 'reg:quantileerror'
            params['quantile_alpha'] = q
            params['eval_metric'] = 'mae'

            model = xgb.XGBRegressor(**params)
            model.fit(X_train, Y_train,
                      eval_set=[(X_val, Y_val)],
                      verbose=False)
            models[channel][q] = model
            actual_rounds = (model.best_iteration + 1) if model.best_iteration else params['n_estimators']
            print(f"q{int(q*100)}[{actual_rounds}rnd]", end=" ")

        print("done")

    print(f"\n  [WIND] Skipped training (will use block bootstrap of historical data)")

    return models, feature_engineers


# =============================================================================
# RESIDUAL CALIBRATION
# =============================================================================
def calibrate_residuals(models: Dict, feature_engineers: Dict,
                        splits: Dict, fast: bool = False) -> Dict:
    """
    Compute data-driven calibration parameters on the validation set.

    For each XGBoost channel the following are estimated:

    sigma_calibration
        Ratio of observed absolute residuals to the model's predicted
        spread ``(q84 − q16) / 2``.  Corrects systematic under- or
        over-estimation of uncertainty.
    rho
        Lag-1 autocorrelation of standardised residuals, used to drive
        AR(1) correlated noise during generation.
    drift_alpha
        Autoregressive drift-correction coefficient, determined via a
        pilot-generation sweep (see ``_compute_drift_alpha``).

    For Solar, calibration is restricted to daytime hours (elevation > 5°
    and observed value > 0.01) to avoid dilution by trivial night-time zeros.
    """
    print("\n[CALIBRATION] Computing data-driven noise parameters...")
    calibration = {}
    hourly_mean = splits['hourly_mean']

    for channel in XGBOOST_CHANNELS:
        fe = feature_engineers[channel]
        ch_idx = CHANNEL_IDX[channel]
        X_val, Y_val = fe.create_training_features(splits['val'])

        q50 = models[channel][0.50].predict(X_val)
        q16 = models[channel][0.16].predict(X_val)
        q84 = models[channel][0.84].predict(X_val)

        predicted_sigma = np.maximum((q84 - q16) / 2.0, 1e-6)
        actual_residual = np.abs(Y_val - q50)
        std_resid = (Y_val - q50) / predicted_sigma
        std_resid = np.clip(std_resid, -10.0, 10.0)

        if channel == "Solar":
            # DAYTIME-ONLY calibration: nighttime zeros destroy rho and sigma_cal
            n_val = len(Y_val)
            val_hours = np.arange(n_val) % 8760
            val_doy = (val_hours // 24) + 1
            val_hod = (val_hours % 24).astype(float)
            val_elev = calculate_solar_elevation(val_doy, val_hod)
            daytime = (val_elev > 5.0) & (Y_val > 0.01)

            if daytime.sum() > 100:
                sigma_cal = float(np.mean(actual_residual[daytime]) /
                                  np.mean(predicted_sigma[daytime]))

                # Rho: only consecutive daytime transitions (both t and t+1 daytime)
                both_day = daytime[:-1] & daytime[1:]
                if both_day.sum() > 100:
                    rho = float(np.corrcoef(std_resid[:-1][both_day],
                                            std_resid[1:][both_day])[0, 1])
                else:
                    rho = 0.3
                print(f"  {channel}: daytime-only calibration ({daytime.sum()} hours)")
            else:
                print(f"  [WARN] {channel}: only {daytime.sum()} daytime hours in val set, "
                      f"using all hours for calibration (may be biased by nighttime zeros).")
                sigma_cal = float(np.mean(actual_residual) / np.mean(predicted_sigma))
                rho = float(np.corrcoef(std_resid[:-1], std_resid[1:])[0, 1])
        else:
            sigma_cal = float(np.mean(actual_residual) / np.mean(predicted_sigma))
            rho = float(np.corrcoef(std_resid[:-1], std_resid[1:])[0, 1])

        if np.isnan(rho):
            rho = 0.0

        calibration[channel] = {
            'sigma_calibration': sigma_cal,
            'rho': rho,
            'residual_mean': float(np.mean(std_resid)),
            'residual_std': float(np.std(std_resid)),
        }

        print(f"  {channel}: sigma_cal={sigma_cal:.3f}, rho={rho:.3f}, "
              f"resid_std={np.std(std_resid):.3f}")

    # Compute drift_alpha via pilot generation for each channel
    if fast:
        print("\n  [DRIFT] Fast mode: using drift_alpha=0.0 (skipping pilot generation)")
        for channel in XGBOOST_CHANNELS:
            calibration[channel]['drift_alpha'] = 0.0
    else:
        print("\n  [DRIFT] Computing optimal drift correction via pilot generation...")
        for channel in XGBOOST_CHANNELS:
            ch_idx = CHANNEL_IDX[channel]
            anchor = hourly_mean[:, ch_idx]
            fe = feature_engineers[channel]
            cal = calibration[channel]

            drift_alpha = _compute_drift_alpha(
                models, channel, fe, anchor,
                cal['sigma_calibration'], cal['rho'],
                hourly_mean, splits['hourly_std']
            )
            calibration[channel]['drift_alpha'] = drift_alpha
            print(f"  {channel}: drift_alpha={drift_alpha:.3f}")

    return calibration


def _compute_drift_alpha(models: Dict, channel: str,
                         fe: 'ChannelFeatureEngineer',
                         anchor: np.ndarray,
                         sigma_cal: float, rho: float,
                         hourly_mean: np.ndarray,
                         hourly_std: np.ndarray) -> float:
    """
    Select the optimal drift-correction coefficient via pilot generation.

    In autoregressive generation the model feeds its own (slightly biased)
    predictions back through lag features, causing the bias to compound over
    8 760 consecutive steps.  A small blending toward the climatological
    anchor counteracts this drift.

    For each candidate alpha in a predefined grid, a single deterministic
    pilot year is generated and its monthly means are compared with the
    historical climatology.  The alpha that minimises monthly-mean RMSE is
    returned.
    """
    ch_idx = CHANNEL_IDX[channel]
    n_hours = HOURS_PER_YEAR
    temp_floor = float(np.min(hourly_mean[:, 2])) - 15.0
    temp_ceil = float(np.max(hourly_mean[:, 2])) + 15.0

    alphas = np.array([0.0, 0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    n_alpha = len(alphas)

    _hpm = [d * 24 for d in [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]]

    # Use GPU boosters directly — we batch all 5 alpha candidates as "scenarios",
    # so each inplace_predict call processes 5 samples (amortizes call overhead).
    bst_q50 = models[channel][0.50].get_booster()
    bst_q16 = models[channel][0.16].get_booster()
    bst_q84 = models[channel][0.84].get_booster()

    # Pre-compute solar elevation for all 8760 hours
    if channel == "Solar":
        _pilot_doy = np.arange(n_hours) // 24 + 1
        _pilot_hod = (np.arange(n_hours) % 24).astype(float)
        _pilot_elev = calculate_solar_elevation(_pilot_doy, _pilot_hod)

    # Pre-generate AR(1) noise ONCE (identical for all alphas)
    np.random.seed(42)
    ar1 = _generate_ar1_noise(n_hours, 1, rho)

    # Batch all 5 alpha candidates as parallel "scenarios"
    hist = np.zeros((n_alpha, 170), dtype=np.float32)
    hist[:] = hourly_mean[-170:, ch_idx]

    pilots = np.zeros((n_alpha, n_hours), dtype=np.float32)

    for t in range(n_hours):
        X_batch = fe.get_batch_feature_vector(t, hist)  # (n_alpha, 16)

        if channel == "Solar":
            if _pilot_elev[t] <= -1.0:
                values = np.zeros(n_alpha, dtype=np.float32)
            else:
                q50 = bst_q50.inplace_predict(X_batch)
                q16 = bst_q16.inplace_predict(X_batch)
                q84 = bst_q84.inplace_predict(X_batch)
                sig = np.maximum(0.0, (q84 - q16) / 2.0) * sigma_cal
                values = q50 + sig * float(ar1[t, 0])
                # Apply different drift alpha per candidate
                anchor_val = anchor[t]
                for a_idx in range(n_alpha):
                    if alphas[a_idx] > 0 and anchor_val > 0.01:
                        values[a_idx] = (1.0 - alphas[a_idx]) * values[a_idx] + alphas[a_idx] * anchor_val
                values = np.clip(values, 0.0, 1.0)
        else:
            q50 = bst_q50.inplace_predict(X_batch)
            q16 = bst_q16.inplace_predict(X_batch)
            q84 = bst_q84.inplace_predict(X_batch)
            sig = np.maximum(0.0, (q84 - q16) / 2.0) * sigma_cal
            values = q50 + sig * float(ar1[t, 0])
            anchor_val = anchor[t]
            for a_idx in range(n_alpha):
                if alphas[a_idx] > 0:
                    values[a_idx] = (1.0 - alphas[a_idx]) * values[a_idx] + alphas[a_idx] * anchor_val
            values = np.clip(values, temp_floor, temp_ceil)

        pilots[:, t] = values
        hist[:, :-1] = hist[:, 1:]
        hist[:, -1] = values

    # Score each alpha: RMSE of monthly means vs climatology
    best_alpha = 0.0
    best_score = float('inf')
    for a_idx in range(n_alpha):
        s_idx = 0
        sq_err = 0.0
        for h in _hpm:
            gen_m = float(pilots[a_idx, s_idx:s_idx + h].mean())
            hist_m = float(anchor[s_idx:s_idx + h].mean())
            sq_err += (gen_m - hist_m) ** 2
            s_idx += h
        rmse = np.sqrt(sq_err / 12.0)
        if rmse < best_score:
            best_score = rmse
            best_alpha = float(alphas[a_idx])

    return best_alpha


# =============================================================================
# HYPERPARAMETER TUNING
# =============================================================================
def tune_channel(fe: 'ChannelFeatureEngineer', splits: Dict,
                 n_trials: int = 20) -> Dict:
    if not OPTUNA_AVAILABLE:
        print("[WARN] Optuna not installed. Skipping tuning.")
        return {}

    X_train, Y_train = fe.create_training_features(splits['train'])
    X_val, Y_val = fe.create_training_features(splits['val'])

    print(f"  [TUNING] {fe.channel} ({n_trials} trials)...")

    def objective(trial):
        params = {
            "tree_method": "hist", "device": "cpu", "random_state": 42,
            "verbosity": 0,
            "n_estimators": trial.suggest_int("n_estimators", 100, 400),
            "max_depth": trial.suggest_int("max_depth", 3, 9),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "objective": "reg:quantileerror",
            "quantile_alpha": 0.84,
            "eval_metric": "mae",
        }
        model = xgb.XGBRegressor(**params)
        model.fit(X_train, Y_train, eval_set=[(X_val, Y_val)], verbose=False)
        preds = model.predict(X_val)
        coverage = np.mean(Y_val < preds)
        coverage_err = abs(coverage - 0.84)
        mae = np.mean(np.abs(Y_val - preds))
        return coverage_err * 10.0 + mae

    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
    return study.best_params


# =============================================================================
# MODEL PERSISTENCE
# =============================================================================
def save_models(models: Dict, feature_engineers: Dict,
                splits: Dict, calibration: Dict):
    os.makedirs(MODELS_DIR, exist_ok=True)

    # Save XGBoost models
    joblib.dump(models, os.path.join(MODELS_DIR, "xgb_models.joblib"))

    save_dict = {
        'hourly_mean': splits['hourly_mean'],
        'hourly_std': splits['hourly_std'],
    }

    # Calibration parameters
    for ch, cal in calibration.items():
        ch_key = ch.lower()
        save_dict[f'{ch_key}_sigma_cal'] = cal['sigma_calibration']
        save_dict[f'{ch_key}_rho'] = cal['rho']
        save_dict[f'{ch_key}_drift_alpha'] = cal.get('drift_alpha', 0.0)

    # Wind training data for block bootstrap
    wind_train = splits['train'][:, CHANNEL_IDX['Wind']]
    save_dict['wind_train_data'] = wind_train

    # Temperature bounds
    temp_train = splits['train'][:, CHANNEL_IDX['Temperature']]
    save_dict['temp_train_min'] = float(np.min(temp_train))
    save_dict['temp_train_max'] = float(np.max(temp_train))

    np.savez(os.path.join(MODELS_DIR, "xgb_norm_params.npz"), **save_dict)
    print(f"  [SAVE] Models and calibration saved to {MODELS_DIR}")


def load_models(path: str = None) -> Tuple[Dict, Dict]:
    path = path or MODELS_DIR
    models_path = os.path.join(path, "xgb_models.joblib")
    params_path = os.path.join(path, "xgb_norm_params.npz")
    if not os.path.exists(models_path):
        raise FileNotFoundError(f"Models not found: {models_path}. Run --train first.")
    if not os.path.exists(params_path):
        raise FileNotFoundError(f"Params not found: {params_path}. Run --train first.")
    models = joblib.load(models_path)
    norm_data = np.load(params_path, allow_pickle=True)
    params = {k: norm_data[k] for k in norm_data.files}
    return models, params


# =============================================================================
# SCENARIO GENERATION
# =============================================================================
def _generate_ar1_noise(n_hours: int, n_scenarios: int, rho: float) -> np.ndarray:
    """
    Generate AR(1) correlated Gaussian noise.

    noise[t] = rho * noise[t-1] + sqrt(1 - rho^2) * eps[t],  eps ~ N(0,1)

    Returns shape ``(n_hours, n_scenarios)``.
    """
    innovation_std = np.sqrt(1.0 - rho ** 2)
    # Pre-draw all innovations in one bulk call (avoids 8760 separate randn calls)
    eps = np.random.randn(n_hours, n_scenarios).astype(np.float32)
    noise = np.zeros((n_hours, n_scenarios), dtype=np.float32)
    noise[0] = eps[0]
    for t in range(1, n_hours):
        noise[t] = rho * noise[t - 1] + innovation_std * eps[t]
    return noise


def _generate_wind_bootstrap(wind_train: np.ndarray, n_hours: int,
                              n_scenarios: int, seed: int = None) -> np.ndarray:
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
    block_len = 144   # ~6 days — matches synoptic weather system duration
    crossfade = 4     # hours crossfade

    # Assign seasons to training data hours
    pool_len = len(wind_train)
    pool_hoy = np.arange(pool_len) % 8760
    _hpm = [d * 24 for d in [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]]
    _cum = np.cumsum([0] + _hpm)
    pool_months = np.zeros(pool_len, dtype=np.int32)
    for m in range(12):
        mask = (pool_hoy >= _cum[m]) & (pool_hoy < _cum[m + 1])
        pool_months[mask] = m

    if CONFIG.latitude >= 0:
        m2s = [0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 0]  # NH
    else:
        m2s = [2, 2, 3, 3, 3, 0, 0, 0, 1, 1, 1, 2]  # SH
    pool_seasons = np.array([m2s[m] for m in pool_months], dtype=np.int32)

    # Per-season valid start indices
    season_starts = {}
    season_names = {0: "Winter (DJF)", 1: "Spring (MAM)", 2: "Summer (JJA)", 3: "Autumn (SON)"}
    for sid in range(4):
        valid = np.where(pool_seasons == sid)[0]
        valid = valid[valid + block_len <= pool_len]
        if len(valid) == 0:
            print(f"  [WARN] Wind bootstrap: season {season_names[sid]} has no valid "
                  f"start positions (need at least {block_len} consecutive hours). "
                  f"Falling back to all-season pool — seasonal structure will be degraded.")
            valid = np.arange(max(1, pool_len - block_len))
        elif len(valid) < 10:
            print(f"  [WARN] Wind bootstrap: season {season_names[sid]} has only "
                  f"{len(valid)} valid start position(s). Bootstrap diversity will be limited.")
        season_starts[sid] = valid

    # Target season per generation hour
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


def generate_scenarios(models: Dict, norm_params: Dict,
                       num_scenarios: int = 100,
                       history_data: np.ndarray = None,
                       seed: int = 42) -> np.ndarray:
    """
    Generate an ensemble of synthetic annual climate profiles.

    Solar and Temperature are produced by autoregressive XGBoost prediction
    with AR(1)-correlated noise.  Wind is produced by season-aware block
    bootstrap of historical data.  All three channels are generated
    independently.

    Parameters
    ----------
    models : dict
        Trained XGBoost models (from ``train_models``).
    norm_params : dict
        Calibration and normalisation parameters (from ``save_models``).
    num_scenarios : int
        Number of synthetic years to generate.
    history_data : np.ndarray, optional
        Raw training data used to seed the autoregressive history buffer.

    Returns
    -------
    np.ndarray
        Shape ``(num_scenarios, 8760, 3)`` — [Solar, Wind, Temperature].
    """
    # Set random seed for full reproducibility (AR(1) noise + history buffer seeding)
    np.random.seed(seed)

    n_hours = HOURS_PER_YEAR
    hourly_mean = norm_params['hourly_mean']
    hourly_std = norm_params['hourly_std']

    # Temperature bounds
    temp_floor = float(norm_params.get('temp_train_min', -30.0)) - 10.0
    temp_ceil = float(norm_params.get('temp_train_max', 50.0)) + 10.0

    scenarios = np.zeros((num_scenarios, n_hours, 3), dtype=np.float32)

    # =====================================================================
    # WIND: Season-aware block bootstrap
    # =====================================================================
    wind_train = norm_params.get('wind_train_data', None)
    if wind_train is not None:
        print("  [WIND] Season-aware block bootstrap of historical data...")
        scenarios[:, :, CHANNEL_IDX['Wind']] = _generate_wind_bootstrap(
            wind_train, n_hours, num_scenarios, seed=seed)
        print(f"    Generated {num_scenarios} wind scenarios (resampled, not modeled).")
    else:
        raise ValueError("Training wind pool is required for seasonal resampling")

    # =====================================================================
    # SOLAR & TEMPERATURE: XGBoost autoregressive with AR(1) noise
    # =====================================================================
    for channel in XGBOOST_CHANNELS:
        ch_idx = CHANNEL_IDX[channel]
        ch_lower = channel.lower()

        sigma_cal = float(norm_params.get(f'{ch_lower}_sigma_cal', 1.5))
        rho = float(norm_params.get(f'{ch_lower}_rho', 0.3))
        drift_alpha = float(norm_params.get(f'{ch_lower}_drift_alpha', 0.0))

        print(f"  [{channel.upper()}] XGBoost generation (sigma_cal={sigma_cal:.3f}, "
              f"rho={rho:.3f}, drift_alpha={drift_alpha:.3f})...")

        # Create feature engineer
        anchor = hourly_mean[:, ch_idx]
        fe = ChannelFeatureEngineer(channel, anchor_profile=anchor, latitude=CONFIG.latitude)

        # Generate AR(1) noise
        ar1_noise = _generate_ar1_noise(n_hours, num_scenarios, rho)

        # Initialize history buffer (single channel)
        hist_len = 170
        history_batch = np.zeros((num_scenarios, hist_len), dtype=np.float32)

        # Seed from historical data or climatology
        if history_data is not None and len(history_data) > hist_len:
            for s in range(num_scenarios):
                start = np.random.randint(0, len(history_data) - hist_len)
                history_batch[s] = history_data[start:start + hist_len, ch_idx]
        else:
            seed_mean = hourly_mean[-hist_len:, ch_idx]
            seed_std = hourly_std[-hist_len:, ch_idx]
            for s in range(num_scenarios):
                seed = seed_mean + seed_std * np.random.randn(hist_len)
                if channel == "Solar":
                    seed = np.clip(seed, 0, 1)
                elif channel == "Temperature":
                    seed = np.clip(seed, temp_floor, temp_ceil)
                history_batch[s] = seed

        # Pre-extract Boosters for fast inplace_predict (avoids DMatrix overhead)
        bst_q50 = models[channel][0.50].get_booster()
        bst_q16 = models[channel][0.16].get_booster()
        bst_q84 = models[channel][0.84].get_booster()

        # Pre-compute solar elevation for all hours (avoids 8760 repeated trig calls)
        if channel == "Solar":
            _gen_doy = np.arange(n_hours) // 24 + 1
            _gen_hod = (np.arange(n_hours) % 24).astype(float)
            _gen_elev = calculate_solar_elevation(_gen_doy, _gen_hod)

        # Autoregressive generation
        for t in range(n_hours):
            X_batch = fe.get_batch_feature_vector(t, history_batch)

            if channel == "Solar":
                if _gen_elev[t] <= -1.0:
                    # Nighttime: solar is exactly 0
                    values = np.zeros(num_scenarios, dtype=np.float32)
                else:
                    q50 = bst_q50.inplace_predict(X_batch)
                    q16 = bst_q16.inplace_predict(X_batch)
                    q84 = bst_q84.inplace_predict(X_batch)
                    sigma = np.maximum(0, (q84 - q16) / 2.0) * sigma_cal
                    values = q50 + sigma * ar1_noise[t]
                    # Drift correction toward climatological anchor
                    anchor_val = anchor[t % 8760]
                    if drift_alpha > 0 and anchor_val > 0.01:
                        values = (1.0 - drift_alpha) * values + drift_alpha * anchor_val
                    values = np.clip(values, 0.0, 1.0)
            else:
                # Temperature
                q50 = bst_q50.inplace_predict(X_batch)
                q16 = bst_q16.inplace_predict(X_batch)
                q84 = bst_q84.inplace_predict(X_batch)
                sigma = np.maximum(0, (q84 - q16) / 2.0) * sigma_cal
                values = q50 + sigma * ar1_noise[t]
                # Drift correction for temperature
                if drift_alpha > 0:
                    values = (1.0 - drift_alpha) * values + drift_alpha * anchor[t % 8760]
                values = np.clip(values, temp_floor, temp_ceil)

            scenarios[:, t, ch_idx] = values

            # Update history buffer
            history_batch[:, :-1] = history_batch[:, 1:]
            history_batch[:, -1] = values

            if (t + 1) % 2000 == 0:
                print(f"    {channel} progress: {t+1}/{n_hours}...")

    # Final physical constraints
    scenarios[:, :, 0] = np.clip(scenarios[:, :, 0], 0.0, 1.0)
    scenarios[:, :, 1] = np.clip(scenarios[:, :, 1], 0.0, 1.0)

    # No CDF matching: the calibrated model does not require post-processing.
    print("  [POST] No CDF matching applied.")

    return scenarios


# =============================================================================
# EVALUATION
# =============================================================================
def evaluate_model(models: Dict, feature_engineers: Dict,
                   data: np.ndarray) -> Tuple[Dict, np.ndarray, np.ndarray]:
    """
    Evaluate point-prediction accuracy of the XGBoost channels on held-out
    data.  Wind is evaluated using a persistence baseline.

    Returns
    -------
    metrics : dict
        Per-channel MAE, RMSE, and bias.
    preds_all : np.ndarray
        Predictions array, shape ``(N - 168, 3)``.
    truth_all : np.ndarray
        Ground-truth array, shape ``(N - 168, 3)``.
    """
    metrics = {}
    n = len(data) - 168
    preds_all = np.zeros((n, 3))
    truth_all = data[168:].copy()

    for channel in XGBOOST_CHANNELS:
        fe = feature_engineers[channel]
        X, Y = fe.create_training_features(data)
        ch_idx = CHANNEL_IDX[channel]

        q50_pred = models[channel][0.50].predict(X)
        mae = np.mean(np.abs(Y - q50_pred))
        rmse = np.sqrt(np.mean((Y - q50_pred) ** 2))
        bias = np.mean(q50_pred - Y)
        metrics[channel] = {'MAE': mae, 'RMSE': rmse, 'BIAS': bias}
        preds_all[:, ch_idx] = q50_pred

    # Wind: no model, just report persistence baseline
    wind_true = data[168:, CHANNEL_IDX['Wind']]
    wind_naive = data[167:-1, CHANNEL_IDX['Wind']]
    metrics['Wind'] = {
        'MAE': np.mean(np.abs(wind_true - wind_naive)),
        'RMSE': np.sqrt(np.mean((wind_true - wind_naive) ** 2)),
        'BIAS': 0.0,
        'note': 'Persistence baseline (no XGBoost model)',
    }
    preds_all[:, CHANNEL_IDX['Wind']] = wind_naive

    return metrics, preds_all, truth_all


def check_overfitting(models, feature_engineers, splits):
    print(f"\n{'Channel':<12} {'Train MAE':<12} {'Val MAE':<12} {'Test MAE':<12}")
    for channel in XGBOOST_CHANNELS:
        fe = feature_engineers[channel]
        maes = []
        for split_name in ['train', 'val', 'test']:
            X, Y = fe.create_training_features(splits[split_name])
            pred = models[channel][0.50].predict(X)
            maes.append(np.mean(np.abs(Y - pred)))
        print(f"{channel:<12} {maes[0]:<12.4f} {maes[1]:<12.4f} {maes[2]:<12.4f}")
    print(f"{'Wind':<12} {'N/A':<12} {'N/A':<12} {'N/A':<12} (block bootstrap)")


def verify_distribution_similarity(Y_true: np.ndarray, Y_synth: np.ndarray) -> Dict:
    """
    Two-sample Kolmogorov–Smirnov test between historical and synthetic
    distributions for each climate channel.
    """
    print(f"\n{'Channel':<12} {'KS D-Stat':<15}")
    results = {}
    synth_flat = Y_synth.reshape(-1, 3)
    true_flat = Y_true.reshape(-1, 3)

    for i, ch in enumerate(ALL_CHANNELS):
        d_stat, _ = ks_2samp(true_flat[:, i], synth_flat[:, i])
        method = "XGBoost" if ch != "Wind" else "Bootstrap"
        print(f"{ch:<12} {d_stat:<15.4f} ({method})")
        results[ch] = {"D": d_stat, "wasserstein": wasserstein_distance(true_flat[:, i], synth_flat[:, i])}
    return results


# =============================================================================
# PLOTTING
# =============================================================================
def plot_test_weeks(Y_true, Y_pred, output_dir, n_weeks=3):
    """Plot test week comparisons for leakage check."""
    n_hours = len(Y_true)
    if n_hours < 170:
        return

    starts = [s for s in [100, 4000, 7000] if s < n_hours - 168]
    for i, start_idx in enumerate(starts):
        end_idx = start_idx + 168
        plt.figure(figsize=(18, 12))
        plt.suptitle(f"Test Week #{i+1} (Hour {start_idx}-{end_idx})", fontsize=16)
        for ch_idx, channel in enumerate(ALL_CHANNELS):
            plt.subplot(3, 1, ch_idx + 1)
            truth = Y_true[start_idx:end_idx, ch_idx]
            pred = Y_pred[start_idx:end_idx, ch_idx]
            plt.plot(truth, 'k-', linewidth=2, label='Ground Truth', alpha=0.7)
            plt.plot(pred, 'r--', linewidth=2, label='XGB Prediction' if channel != 'Wind' else 'Persistence')
            mae = np.mean(np.abs(truth - pred))
            plt.title(f"{channel} (MAE: {mae:.4f})")
            plt.legend()
            plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(os.path.join(output_dir, f"leakage_check_week_{i+1}.png"))
        plt.close()
    print("  [DONE] Test week plots saved.")


def compare_with_history(models, norm_params, raw_data, output_dir,
                         precomputed_ensemble=None):
    """Compare synthetic scenarios vs historical data."""
    print("\n[COMPARISON] Synthetic vs Historical...")
    num_years = len(raw_data) // 8760
    if num_years < 3:
        print("  [ERR] Not enough years for comparison.")
        return

    year_indices = [num_years // 4, num_years // 2, 3 * num_years // 4]
    hist_years = {}
    for idx in year_indices:
        start = idx * 8760
        if start + 8760 <= len(raw_data):
            hist_years[f"Year {idx+1}/{num_years}"] = raw_data[start:start + 8760]

    # Use precomputed ensemble or generate new one
    if precomputed_ensemble is not None:
        ensemble = precomputed_ensemble
        n_ensemble = len(ensemble)
    else:
        seed_data = split_data(raw_data)['train']
        n_ensemble = 50
        ensemble = generate_scenarios(models, norm_params, num_scenarios=n_ensemble,
                                      history_data=seed_data)

    scenario_median = np.median(ensemble, axis=0)
    scenario_p10 = np.percentile(ensemble, 10, axis=0)
    scenario_p90 = np.percentile(ensemble, 90, axis=0)

    # Monthly means
    _dpm = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    _hpm = [d * 24 for d in _dpm]

    def monthly_means(data):
        result = np.zeros((12, 3))
        start = 0
        for m, h in enumerate(_hpm):
            result[m] = data[start:start + h].mean(axis=0)
            start += h
        return result

    months = range(1, 13)
    colors = ['blue', 'green', 'purple']

    fig = plt.figure(figsize=(20, 15))
    for i, ch in enumerate(ALL_CHANNELS):
        method = "XGBoost" if ch != "Wind" else "Bootstrap (Resampled)"

        # Monthly profile
        ax1 = plt.subplot(3, 2, i * 2 + 1)
        for j, (label, data) in enumerate(hist_years.items()):
            ax1.plot(months, monthly_means(data)[:, i], 'o--', label=label,
                     color=colors[j % len(colors)], alpha=0.6)
        ax1.plot(months, monthly_means(scenario_median)[:, i], 'o-',
                 label='Synthetic (Median)', color='red', linewidth=3)
        ax1.fill_between(months, monthly_means(scenario_p10)[:, i],
                         monthly_means(scenario_p90)[:, i], color='red', alpha=0.15)
        ax1.set_title(f"{ch} - Monthly Average [{method}]")
        if i == 0:
            ax1.legend(fontsize=8)
        ax1.grid(True, alpha=0.3)

        # KDE
        ax2 = plt.subplot(3, 2, i * 2 + 2)
        import seaborn as sns
        for j, (label, data) in enumerate(hist_years.items()):
            sns.kdeplot(data[:, i], ax=ax2, label=label, color=colors[j], fill=False, alpha=0.6)
        red_shades = ['#FF0000', '#CC3333', '#FF6666']
        for k in range(min(3, n_ensemble)):
            sns.kdeplot(ensemble[k, :, i], ax=ax2, label=f'Synth #{k+1}',
                       color=red_shades[k], fill=(k == 0), alpha=0.3 if k == 0 else 0.5,
                       linewidth=1.5, linestyle='--' if k > 0 else '-')
        ax2.set_title(f"{ch} - KDE [{method}]")
        ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "comparison_history_vs_synthetic.png"), dpi=150)
    plt.close()

    # Weekly hourly comparison
    scenario_single = ensemble[0]
    _plot_weekly(scenario_single, hist_years, output_dir)

    # KS statistics
    true_flat = np.concatenate([d for d in hist_years.values()])
    verify_distribution_similarity(true_flat, ensemble)


def _plot_weekly(synth_year, hist_years, output_dir, n_weeks=4):
    week_starts = [14, 105, 196, 287]
    season_names = ['Winter (Jan)', 'Spring (Apr)', 'Summer (Jul)', 'Autumn (Oct)']
    colors = ['blue', 'green', 'purple']

    fig, axes = plt.subplots(n_weeks, 3, figsize=(24, 5 * n_weeks))
    fig.suptitle('Weekly Comparison: Synthetic vs Historical', fontsize=16, y=1.01)

    for w, (start_day, season) in enumerate(zip(week_starts, season_names)):
        start_h = start_day * 24
        end_h = start_h + 168
        if end_h > 8760:
            continue
        hours_x = np.arange(168)

        for c, ch in enumerate(ALL_CHANNELS):
            ax = axes[w, c]
            for j, (label, data) in enumerate(hist_years.items()):
                ax.plot(hours_x, data[start_h:end_h, c], alpha=0.5,
                       color=colors[j], linewidth=0.8, label=label)
            method = "XGBoost" if ch != "Wind" else "Resampled"
            ax.plot(hours_x, synth_year[start_h:end_h, c], color='red',
                   linewidth=1.5, alpha=0.9, label=f'Synthetic ({method})')
            if w == 0:
                ax.set_title(ch, fontsize=13, fontweight='bold')
            if c == 0:
                ax.set_ylabel(f'{season}\nValue', fontsize=10)
            ax.grid(True, alpha=0.2)
            if w == 0 and c == 0:
                ax.legend(fontsize=7, loc='upper right')
            for d in range(1, 7):
                ax.axvline(x=d * 24, color='gray', linestyle=':', alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "comparison_weekly_hourly.png"),
                dpi=150, bbox_inches='tight')
    plt.close()


def run_shap_analysis(models, feature_engineers, splits, output_dir):
    try:
        import shap
        n_channels = len(XGBOOST_CHANNELS)
        fig, axes = plt.subplots(1, n_channels, figsize=(9 * n_channels, 6))
        if n_channels == 1:
            axes = [axes]
        for i, channel in enumerate(XGBOOST_CHANNELS):
            fe = feature_engineers[channel]
            X, _ = fe.create_training_features(splits['test'])
            bg = X[:100]
            expl = shap.TreeExplainer(models[channel][0.50])
            plt.sca(axes[i])
            shap.summary_plot(expl.shap_values(bg), bg, plot_type="bar",
                            feature_names=fe.feature_names, show=False)
            axes[i].set_title(f"{channel} Feature Importance")
        plt.savefig(os.path.join(output_dir, "shap_analysis.png"), bbox_inches='tight')
        plt.close()
        print("  [SHAP] Feature importance plots saved.")
    except Exception as e:
        print(f"  [SHAP] Failed: {e}")


# =============================================================================
# COMPREHENSIVE EVALUATION
# =============================================================================
def comprehensive_evaluation(models, feature_engineers, splits, norm_params, output_dir,
                             precomputed_ensemble=None):
    print("\n" + "=" * 60)
    print("COMPREHENSIVE EVALUATION")
    print("=" * 60)

    # Overfitting check
    check_overfitting(models, feature_engineers, splits)

    # Test set evaluation
    metrics, Y_pred, Y_true = evaluate_model(models, feature_engineers, splits['test'])
    print(f"\n{'Channel':<12} {'MAE':<10} {'RMSE':<10} {'BIAS':<10}")
    for ch in ALL_CHANNELS:
        m = metrics[ch]
        note = m.get('note', '')
        print(f"{ch:<12} {m['MAE']:<10.4f} {m['RMSE']:<10.4f} {m['BIAS']:<10.4f} {note}")

    # Leakage check plots
    plot_test_weeks(Y_true, Y_pred, output_dir)

    # Use precomputed ensemble or generate new one for distribution comparison
    if precomputed_ensemble is not None:
        ensemble = precomputed_ensemble
    else:
        ensemble = generate_scenarios(models, norm_params, num_scenarios=20,
                                      history_data=splits['train'])
    verify_distribution_similarity(Y_true, ensemble)

    # SHAP analysis
    run_shap_analysis(models, feature_engineers, splits, output_dir)


# =============================================================================
# OUT-OF-SAMPLE VALIDATION
# =============================================================================
def validate_out_of_sample(raw_data: np.ndarray, use_gpu: bool = False):
    """
    Leave-one-year-out cross-validation of the full generation pipeline.

    For each held-out test year the model is re-trained on the remaining
    data, an ensemble of synthetic scenarios is generated, and the
    distributional match is assessed via the KS statistic.  Results are
    summarised across all folds and saved as a bar chart.
    """
    num_years = len(raw_data) // HOURS_PER_YEAR
    if num_years < 4:
        print("[VALIDATE] Need at least 4 years of data for meaningful validation.")
        return

    years_data = raw_data[:num_years * HOURS_PER_YEAR].reshape(num_years, HOURS_PER_YEAR, 3)

    # Test on last held-out year
    n_test = 1
    test_years = list(range(num_years - n_test, num_years))

    print(f"\n{'='*60}")
    print(f"OUT-OF-SAMPLE VALIDATION ({n_test} held-out years)")
    print(f"{'='*60}")

    all_ks = {ch: [] for ch in ALL_CHANNELS}

    for test_yr in test_years:
        print(f"\n--- Held-out year {test_yr+1}/{num_years} ---")

        # Build train/val from remaining years
        mask = np.ones(num_years, dtype=bool)
        mask[test_yr] = False
        remaining = years_data[mask].reshape(-1, 3)

        n_rem = len(remaining) // HOURS_PER_YEAR
        n_train = max(1, int(n_rem * 0.85))
        train_flat = remaining[:n_train * HOURS_PER_YEAR]
        val_flat = remaining[n_train * HOURS_PER_YEAR:]

        if len(val_flat) < HOURS_PER_YEAR:
            val_flat = train_flat[-HOURS_PER_YEAR:]

        train_3d = train_flat.reshape(-1, HOURS_PER_YEAR, 3)
        splits = {
            'train': train_flat,
            'val': val_flat,
            'test': years_data[test_yr].reshape(-1, 3),
            'hourly_mean': train_3d.mean(axis=0),
            'hourly_std': train_3d.std(axis=0),
        }

        # Train
        models, fes = train_models(splits, use_gpu=use_gpu)
        cal = calibrate_residuals(models, fes, splits, fast=True)

        # Build norm_params
        norm_params = {
            'hourly_mean': splits['hourly_mean'],
            'hourly_std': splits['hourly_std'],
            'wind_train_data': splits['train'][:, CHANNEL_IDX['Wind']],
            'temp_train_min': float(np.min(splits['train'][:, CHANNEL_IDX['Temperature']])),
            'temp_train_max': float(np.max(splits['train'][:, CHANNEL_IDX['Temperature']])),
        }
        for ch, c in cal.items():
            ch_key = ch.lower()
            norm_params[f'{ch_key}_sigma_cal'] = c['sigma_calibration']
            norm_params[f'{ch_key}_rho'] = c['rho']
            norm_params[f'{ch_key}_drift_alpha'] = c.get('drift_alpha', 0.0)

        # Generate (5 scenarios sufficient for KS test: 5*8760=43800 samples)
        ensemble = generate_scenarios(models, norm_params, num_scenarios=5,
                                       history_data=splits['train'])

        # Compare with held-out year
        true_year = years_data[test_yr]  # (8760, 3)
        synth_flat = ensemble.reshape(-1, 3)
        true_flat = true_year.reshape(-1, 3)

        print(f"\n  {'Channel':<12} {'KS D-Stat':<15}")
        for i, ch in enumerate(ALL_CHANNELS):
            d_stat, _ = ks_2samp(true_flat[:, i], synth_flat[:, i])
            method = "XGBoost" if ch != "Wind" else "Bootstrap"
            print(f"  {ch:<12} {d_stat:<15.4f} ({method})")
            all_ks[ch].append(d_stat)

    # Summary
    print(f"\n{'='*60}")
    print("VALIDATION SUMMARY (averaged over held-out years)")
    print(f"{'='*60}")
    print(f"  {'Channel':<12} {'Mean KS':<12} {'Max KS':<12}")
    for ch in ALL_CHANNELS:
        if all_ks[ch]:
            mean_ks = np.mean(all_ks[ch])
            max_ks = np.max(all_ks[ch])
            print(f"  {ch:<12} {mean_ks:<12.4f} {max_ks:<12.4f}")

    # Plot validation results
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle('Out-of-Sample Validation: KS Statistics', fontsize=14)
    for i, ch in enumerate(ALL_CHANNELS):
        ax = axes[i]
        ax.bar(range(len(all_ks[ch])), all_ks[ch], color=['green' if d < 0.08 else 'orange' if d < 0.18 else 'red' for d in all_ks[ch]])
        ax.axhline(y=0.08, color='green', linestyle='--', alpha=0.5, label='EXCELLENT threshold')
        ax.axhline(y=0.18, color='orange', linestyle='--', alpha=0.5, label='GOOD threshold')
        ax.set_title(f'{ch}')
        ax.set_xlabel('Held-out year')
        ax.set_ylabel('KS D-Statistic')
        ax.set_ylim(0, max(0.3, max(all_ks[ch]) * 1.2) if all_ks[ch] else 0.3)
        if i == 0:
            ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(os.path.join(RESULTS_DIR, "out_of_sample_validation.png"), dpi=150)
    plt.close()
    print(f"\n  [SAVE] Validation plot saved to {RESULTS_DIR}/out_of_sample_validation.png")


# =============================================================================
# MAIN
# =============================================================================
def _detect_gpu() -> bool:
    """Check if XGBoost CUDA acceleration is available."""
    try:
        _test = xgb.XGBRegressor(device='cuda', tree_method='hist', n_estimators=1, verbosity=0)
        _test.fit([[0, 1], [1, 0]], [0, 1])
        print(f"[GPU] XGBoost {xgb.__version__} CUDA verified OK")
        return True
    except Exception as e:
        print(f"[GPU] Falling back to CPU ({e})")
        return False


def main(args_list=None):
    parser = argparse.ArgumentParser(description="XGBoost Climate Scenario Generator")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--tune", action="store_true")
    parser.add_argument("--validate", action="store_true",
                        help="Run leave-one-year-out validation (proof of quality)")
    parser.add_argument("--num_scenarios", type=int, default=100)
    parser.add_argument("--country", type=str, default=None)
    parser.add_argument("--latitude", type=float, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")

    if args_list:
        args, _ = parser.parse_known_args(args_list)
    else:
        args, _ = parser.parse_known_args()

    global COUNTRY, CONFIG, HOURS_PER_YEAR, RESULTS_DIR, MODELS_DIR
    if args.country:
        COUNTRY = args.country
    CONFIG = get_config(config_path=args.config, latitude=args.latitude)
    HOURS_PER_YEAR = CONFIG.hours_per_year
    RESULTS_DIR = CONFIG.results_dir
    MODELS_DIR = CONFIG.models_dir

    np.random.seed(args.seed)
    print(f"[SEED] Random seed set to {args.seed}")

    use_gpu = _detect_gpu()
    data = None

    if args.train:
        data = load_climate_data()
        splits = split_data(data)

        custom_params = None
        if args.tune:
            print("\n[TUNING] Hyperparameter optimization...")
            custom_params = {}
            hourly_mean = splits['hourly_mean']
            for ch in XGBOOST_CHANNELS:
                ch_idx = CHANNEL_IDX[ch]
                fe = ChannelFeatureEngineer(ch, anchor_profile=hourly_mean[:, ch_idx],
                                            latitude=CONFIG.latitude)
                custom_params[ch] = tune_channel(fe, splits)

        # Train
        models, feature_engineers = train_models(splits, use_gpu=use_gpu,
                                                  custom_params=custom_params)

        # Calibrate
        calibration = calibrate_residuals(models, feature_engineers, splits)

        # Save
        save_models(models, feature_engineers, splits, calibration)

        # Reload params (includes calibration)
        _, eval_params = load_models()

        # Generate scenarios ONCE and reuse for eval, compare, and saving
        # This avoids generating 20+50+100 = 170 redundant scenarios.
        n_gen = max(args.num_scenarios, 20) if args.generate else 20
        ensemble_all = generate_scenarios(models, eval_params, num_scenarios=n_gen,
                                          history_data=splits['train'], seed=args.seed)

        # Evaluate (reuse first 20 scenarios from ensemble)
        comprehensive_evaluation(models, feature_engineers, splits, eval_params,
                                 RESULTS_DIR, precomputed_ensemble=ensemble_all[:20])

        # Compare (reuse first 50 scenarios, or all if fewer)
        if args.compare:
            n_compare = min(50, len(ensemble_all))
            compare_with_history(models, eval_params, data, RESULTS_DIR,
                                 precomputed_ensemble=ensemble_all[:n_compare])

        # Save scenarios if --generate was requested
        if args.generate:
            data_sc_dir = os.path.dirname(CONFIG.data_path)
            os.makedirs(data_sc_dir, exist_ok=True)
            out_path = os.path.join(data_sc_dir, "xgb_synthetic_scenarios.npy")
            np.save(out_path, ensemble_all[:args.num_scenarios])
            print(f"  [SAVE] {args.num_scenarios} scenarios saved to {out_path}")

    elif args.compare:
        data = load_climate_data()
        models, params = load_models()
        compare_with_history(models, params, data, RESULTS_DIR)

    elif args.generate:
        models, params = load_models()
        # Load historical data for proper AR buffer seeding (consistent with --train path)
        if data is None:
            data = load_climate_data()
        seed_data = split_data(data)['train']
        scenarios = generate_scenarios(models, params, args.num_scenarios,
                                       history_data=seed_data, seed=args.seed)
        # Save next to the data parquet so scenario_manager.py can find it
        data_sc_dir = os.path.dirname(CONFIG.data_path)
        os.makedirs(data_sc_dir, exist_ok=True)
        out_path = os.path.join(data_sc_dir, "xgb_synthetic_scenarios.npy")
        np.save(out_path, scenarios)
        print(f"  [SAVE] {args.num_scenarios} scenarios saved to {out_path}")

    if args.validate:
        if data is None:
            data = load_climate_data()
        validate_out_of_sample(data, use_gpu=use_gpu)


if __name__ == "__main__":
    is_jupyter = 'ipykernel' in sys.modules or any(
        'jupyter' in arg or 'colab' in arg for arg in sys.argv)

    if is_jupyter:
        has_user_args = any(arg.startswith('--') for arg in sys.argv)
        if not has_user_args:
            print("[INFO] Jupyter/Colab detected. Running FULL PIPELINE.")
            main(['--train', '--compare', '--generate', '--validate'])
        else:
            main()
    elif len(sys.argv) == 1:
        print("[INFO] No arguments. Running FULL PIPELINE.")
        main(['--train', '--compare', '--generate', '--validate'])
    else:
        main()

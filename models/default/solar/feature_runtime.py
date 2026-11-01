import numpy as np
from typing import Tuple
from types import SimpleNamespace
CONFIG=SimpleNamespace(**{'latitude': 44.52, 'longitude': 16.798, 'solar_sites': [[43.788, 16.001], [45.311, 18.235], [45.865, 16.206]]})
CHANNEL_IDX={'Solar':0,'Wind':1,'Temperature':2}
def calculate_solar_elevation(day_of_year, hour, lat=None, longitude=None):
    """NOAA solar elevation for UTC clock time and east-positive longitude."""
    if lat is None:
        lat = CONFIG.latitude
    if longitude is None:
        longitude = CONFIG.longitude
    gamma = 2 * np.pi / 365 * (np.asarray(day_of_year) - 1 + (np.asarray(hour) - 12) / 24)
    equation_of_time = 229.18 * (7.5e-05 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma) - 0.014615 * np.cos(2 * gamma) - 0.040849 * np.sin(2 * gamma))
    dec_rad = 0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma) - 0.006758 * np.cos(2 * gamma) + 0.000907 * np.sin(2 * gamma) - 0.002697 * np.cos(3 * gamma) + 0.00148 * np.sin(3 * gamma)
    hour_angle = (np.asarray(hour) * 60 + equation_of_time + 4 * longitude) / 4 - 180
    lat_rad = np.radians(lat)
    sha_rad = np.radians(hour_angle)
    sin_elev = np.sin(lat_rad) * np.sin(dec_rad) + np.cos(lat_rad) * np.cos(dec_rad) * np.cos(sha_rad)
    return np.degrees(np.arcsin(np.clip(sin_elev, -1, 1)))

def solar_interval_elevation(day_of_year, hour, lat=None):
    """Keep an hourly mean supported whenever any source site has daylight."""
    sites = CONFIG.solar_sites or ((lat if lat is not None else CONFIG.latitude, CONFIG.longitude),)
    values = [calculate_solar_elevation(day_of_year, np.asarray(hour) + offset, latitude, longitude) for latitude, longitude in sites for offset in (0.0, 0.5, 1.0)]
    return np.maximum.reduce(values)

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
    return np.divide(cumulative[end] - cumulative[start], counts, out=np.zeros(len(values), dtype=float), where=counts > 0)

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

    def __init__(self, channel: str, anchor_profile: np.ndarray=None, latitude: float=None):
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
        self.anchor_profile = anchor_profile
        self.feature_names = []
        ch_lower = channel.lower()[:4]
        self.feature_names += ['sin_hour', 'cos_hour', 'sin_day', 'cos_day', 'is_day', 'is_summer', 'hour_norm', 'day_norm']
        if self.anchor_profile is not None:
            self.feature_names.append(f'anchor_{ch_lower}')
        for lag in self.lags:
            self.feature_names.append(f'lag_{lag}_{ch_lower}')
        for w in self.rolling_windows:
            self.feature_names.append(f'roll_{w}_{ch_lower}')
        self.feature_names += [f'diff1_{ch_lower}', f'diff24_{ch_lower}']
        self.feature_map = {n: i for i, n in enumerate(self.feature_names)}
        self.n_features = len(self.feature_names)
        self._static_8760 = self._make_static(8760)

    def _make_static(self, n):
        hours = np.arange(n) % 24
        days = np.arange(n) // 24 % 365
        feats = [np.sin(2 * np.pi * hours / 24), np.cos(2 * np.pi * hours / 24), np.sin(2 * np.pi * days / 365), np.cos(2 * np.pi * days / 365), (solar_interval_elevation(days + 1, hours.astype(float), self.latitude) > 0).astype(np.float32), ((days >= 91) & (days <= 273)).astype(np.float32) if self.latitude >= 0 else ((days < 91) | (days > 273)).astype(np.float32), hours / 24.0, days / 365.0]
        return np.stack(feats, axis=1).astype(np.float32)

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
        if n == 8760:
            static = self._static_8760
        else:
            static = self._make_static(n)
        feats = [static]
        if self.anchor_profile is not None:
            n_rep = n // 8760 + 1
            anchor = np.tile(self.anchor_profile, n_rep)[:n]
            feats.append(anchor.reshape(-1, 1))
        lag_feats = []
        for lag in self.lags:
            lag_feats.append(_safe_shift(values, lag, fill_val=np.mean(values)))
        feats.append(np.stack(lag_feats, axis=1))
        roll_feats = []
        for w in self.rolling_windows:
            roll_feats.append(_past_rolling(values, w))
        feats.append(np.stack(roll_feats, axis=1))
        l1 = _safe_shift(values, 1, fill_val=np.mean(values))
        l2 = _safe_shift(values, 2, fill_val=np.mean(values))
        l24 = _safe_shift(values, 24, fill_val=np.mean(values))
        feats.append(np.stack([l1 - l2, l1 - l24], axis=1))
        X = np.concatenate(feats, axis=1).astype(np.float32)
        return (X[168:], Y[168:].astype(np.float32))

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
        if self.anchor_profile is not None:
            X[:, idx] = self.anchor_profile[hour_in_year]
            idx += 1
        for lag in self.lags:
            if hist_len >= lag:
                X[:, idx] = history_batch[:, -lag]
            idx += 1
        for w in self.rolling_windows:
            if hist_len >= w:
                X[:, idx] = np.mean(history_batch[:, -w:], axis=1)
            idx += 1
        if hist_len >= 2:
            X[:, idx] = history_batch[:, -1] - history_batch[:, -2]
        idx += 1
        if hist_len >= 24:
            X[:, idx] = history_batch[:, -1] - history_batch[:, -24]
        idx += 1
        return X

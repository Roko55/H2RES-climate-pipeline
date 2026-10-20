from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_targets(frame, technology, cutoff=2015):
    selected = frame[(frame['ISO3 code'].astype(str).str.strip() == 'HRV') &
                     frame.Technology.astype(str).str.lower().str.contains(technology, regex=False)].copy()
    columns = ['Year', 'Electricity Installed Capacity (MW)', 'Electricity Generation (GWh)']
    for column in columns:
        selected[column] = pd.to_numeric(selected[column], errors='coerce')
    cap, gen = selected[columns[1]], selected[columns[2]]
    selected = selected[(selected.Year >= 1980) & (selected.Year <= cutoff) &
                        (cap >= (10.0 if technology == 'solar photovoltaic' else 1e-12)) & (gen > 0)].copy()
    selected['cf'] = selected[columns[2]] * 1000 / (selected[columns[1]] * 8760)
    selected = selected[(selected.cf > 0) & (selected.cf < 1)]
    if selected.Year.duplicated().any():
        raise ValueError('Ambiguous annual target rows')
    if technology == 'solar photovoltaic':
        selected = selected.sort_values('Year').tail(5)
    if selected.empty:
        raise ValueError('No eligible training-period targets: ' + technology)
    return {int(row.Year): float(row.cf) for row in selected.itertuples()}


def fit_alpha(values, weights, target):
    values, weights = np.asarray(values, dtype=float), np.asarray(weights, dtype=float)
    if values.ndim != 2 or weights.ndim != 1 or values.shape[1] != len(weights):
        raise ValueError('Expected [hours, sites] and one weight per site')
    if not np.isfinite(values).all() or not np.isfinite(weights).all() or not np.isfinite(target):
        raise ValueError('Nonfinite calibration input')
    if len(values) == 0 or not 0 < target < 1 or (values < 0).any() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError('Invalid calibration input')
    weights = weights / weights.sum()
    def mean(alpha):
        return float(np.clip(alpha * values, 0, 1).mean(axis=0) @ weights)
    lo, hi = .1, 5.
    if not mean(lo) <= target <= mean(hi):
        raise ValueError('Calibration target is outside scale bounds')
    for _ in range(50):
        mid = (lo + hi) / 2
        if mean(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def strict_alpha(values, weights, years, targets):
    values, years = np.asarray(values), np.asarray(years)
    if values.ndim != 2 or years.ndim != 1 or len(values) != len(years):
        raise ValueError('One year is required per input row')
    if not targets or min(targets) < 1980 or max(targets) > 2015:
        raise ValueError('Calibration targets must belong to 1980-2015')
    training = (years >= 1980) & (years <= 2015)
    return fit_alpha(values[training], weights, float(np.mean(list(targets.values()))))


def prepare_archive(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError('Choose a new output directory')
    old = pd.read_parquet(source / 'HR_data.parquet')
    dates = pd.DatetimeIndex(pd.to_datetime(old.time, utc=True)).tz_localize(None)
    if not dates.equals(pd.date_range('1980-01-01', '2024-12-31 23:00', freq='h')):
        raise ValueError('Expected a complete 1980-2024 hourly archive')
    sources = {}
    def load(path, column):
        sources[path.relative_to(source).as_posix()] = digest(path)
        frame = pd.read_parquet(path)
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True)).tz_localize(None)
        if frame.index.duplicated().any():
            raise ValueError('Duplicate raw timestamps: ' + path.name)
        values = frame[column].reindex(dates).to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError('Raw data contain gaps: ' + path.name)
        return values
    meta_path = source / 'HR_macro_climate_metadata.csv'
    sources[meta_path.name] = digest(meta_path)
    meta = pd.read_csv(meta_path)
    solar, temperature = np.zeros(len(dates)), np.zeros(len(dates))
    for channel, destination, prefix in [('solar', solar, 'solar'), ('temperature', temperature, 'temp')]:
        rows = meta[meta.channel == channel]
        weights = rows.weight_value.to_numpy(dtype=float)
        if len(rows) != 3 or not np.isfinite(weights).all() or (weights <= 0).any():
            raise ValueError('Expected three weighted source zones')
        for row in rows.itertuples():
            destination += load(source / 'raw_data' / f'{prefix}_{row.zone_name}.parquet', channel) * row.weight_value / weights.sum()
    site_path = source / 'HR_wind_sites_meta.csv'
    sources[site_path.name] = digest(site_path)
    sites = pd.read_csv(site_path)
    wind = np.empty((len(dates), len(sites)))
    for i, row in enumerate(sites.itertuples()):
        candidates = sorted((source / 'raw_data/openmeteo_wind').glob(f'*_{row.lat:.3f}_{row.lon:.3f}.parquet'))
        if not candidates:
            raise FileNotFoundError('Missing wind cache for ' + row.column)
        wind[:, i] = load(candidates[0], 'cf')
        for duplicate in candidates[1:]:
            if not np.array_equal(wind[:, i], load(duplicate, 'cf')):
                raise ValueError('Conflicting caches at identical coordinates')
    weights = sites.cap_mw.to_numpy(dtype=float)
    if not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError('Invalid site capacities')
    weights /= weights.sum()
    workbook = source / 'IRENA_Statistics_Extract_2025H2.xlsx'
    sources[workbook.name] = digest(workbook)
    irena = pd.read_excel(workbook, sheet_name='Country')
    targets = {name: select_targets(irena, technology) for name, technology in
               [('solar', 'solar photovoltaic'), ('wind', 'onshore wind')]}
    alphas = {'solar': strict_alpha(solar[:, None], np.ones(1), dates.year, targets['solar']),
              'wind': strict_alpha(wind, weights, dates.year, targets['wind'])}
    solar_metadata = json.loads((source / 'HR_solar_calibration_metadata.json').read_text())
    wind_metadata = json.loads((source / 'HR_calibration_metadata.json').read_text())
    replay = {'solar': float(np.max(np.abs(np.clip(solar * solar_metadata['calibration_alpha'], 0, 1) - old.solar))),
              'wind': float(np.max(np.abs(np.clip(wind * wind_metadata['calibration_alpha'], 0, 1) @ weights - old.wind))),
              'temperature': float(np.max(np.abs(temperature - old.temperature)))}
    if max(replay.values()) > 5e-6:
        raise ValueError('Raw sources do not reproduce the supplied archive')
    frame = pd.DataFrame({'time': dates, 'solar': np.clip(solar * alphas['solar'], 0, 1),
                          'wind': np.clip(wind * alphas['wind'], 0, 1) @ weights, 'temperature': temperature})
    output.mkdir(parents=True, exist_ok=False)
    path = output / 'HR_data_training_calibrated.parquet'
    frame.to_parquet(path, index=False)
    record = {'raw_sources': sources, 'calibration_targets': targets, 'calibration_alpha': alphas,
              'calibration_years': [1980, 2015], 'output_sha256': digest(path), 'source_replay_max_abs': replay,
              'reporting_period_previously_inspected': True}
    (output / 'preprocessing_manifest.json').write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
    return path

"""Remap frozen Croatian profiles to dated zonal stock, without refitting."""

import csv
from hashlib import sha256
import importlib.util
import json
from math import fsum, isclose
from pathlib import Path
import socket
import sys


SPATIAL = {
    'solar': ('HR_solar_macro_profiles.parquet', 'HR_solar_transfer_macro_to_units.csv', 'solar_cf', 3),
    'wind': ('HR_wind_nuts_profiles.parquet', 'HR_wind_transfer_nuts_to_units.csv', 'wind_cf', 5),
}


def digest(path):
    with path.open('rb') as stream:
        h = sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def capacities(root):
    solar = json.loads((root/'solar_stock_2025_preflight/preflight.json').read_bytes())
    wind = json.loads((root/'wind_stock_zone_preflight/preflight.json').read_bytes())
    rows = {'solar': {v['unit_name']: v['candidate_connection_mw'] for v in solar['candidate_by_zone'].values()},
            'wind': {'HR_Wind_'+k: v['installed_mw'] for k, v in wind['stock_by_zone']['2025']['regular'].items()}}
    for channel, total in (('solar', 1278.742), ('wind', 1181.15)):
        if len(rows[channel]) != SPATIAL[channel][3] or not isclose(fsum(rows[channel].values()), total, abs_tol=1e-7):
            raise ValueError('Dated stock total differs for '+channel)
    return rows


def write_transfer(template, target, stock, count):
    with template.open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    fields = ['unit_name','source_zone','weight','source_cap_mw','unit_cap_mw']
    if (len(rows) != count or {r['unit_name'] for r in rows} != set(stock)
            or len({r['source_zone'] for r in rows}) != count
            or any(float(r['weight']) != 1. or not r['unit_name'].endswith('_'+r['source_zone']) for r in rows)):
        raise ValueError('Unexpected native unit/source correspondence')
    with target.open('x', newline='', encoding='utf8') as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for r in rows:
            mw = stock[r['unit_name']]
            writer.writerow(dict(r, source_cap_mw=mw, unit_cap_mw=mw))


def install_curves(native, frozen_profile, sources, stocks, adapter):
    import numpy as np

    hours = list(range(1,8761))
    section = native['year'].eq(2030)
    frozen_profile = frozen_profile.loc[frozen_profile['year'].eq(2030)]
    if list(native.loc[section,'period']) != hours or list(frozen_profile['period']) != hours:
        raise ValueError('Expected aligned complete 2030 calendars')
    output = native.copy()
    receipt = {}
    for channel, (_, _, field, count) in SPATIAL.items():
        stock = stocks[channel]
        names = list(stock)
        source, transfer = sources[channel]
        if len(names) != count or not set(names) <= set(native.columns):
            raise ValueError('Expected active Croatian spatial columns')
        profile = frozen_profile[field].to_numpy(dtype=float)
        if not np.isfinite(profile).all() or np.any((profile<0.)|(profile>1.)):
            raise ValueError('Frozen national profile is outside physical bounds')
        mapped, audit = adapter(profile, source, transfer, stock)
        values = mapped[names].to_numpy(dtype=float)
        if (values.shape != (8760,count) or audit['spatial_reference_end_year'] != 2015
                or audit['source_zone_count'] != count or not np.isfinite(values).all()
                or np.any((values<0.)|(values>1.))):
            raise ValueError('Unqualified spatial input for '+channel)
        delta = float(np.max(np.abs(np.average(values, axis=1, weights=list(stock.values()))-profile)))
        if delta > 1e-8 or np.any(values[profile == 0.] != 0.):
            raise ValueError('Spatial remap does not preserve the national profile')
        output.loc[section,names] = values
        receipt[channel] = dict(max_hourly_cf_error=delta, unit_mw=stock, training_rows=audit['spatial_reference_rows'])
    return output, receipt


def verify_training_source(path, lineage_path, channel):
    import pandas as pd

    meta = json.loads(Path(lineage_path).read_bytes())
    if meta['channel'] != channel or meta['includes_held_out_observations'] is not False or digest(path) != meta['profile_sha256']:
        raise ValueError('Spatial training source does not match approved lineage')
    frame = pd.read_parquet(path)
    times = pd.DatetimeIndex(pd.to_datetime(frame.get('time',frame.index), utc=True))
    if (times.hasnans or not times.is_unique or not times.is_monotonic_increasing
            or times[-1] != pd.Timestamp('2015-12-31T23:00:00Z')):
        raise ValueError('Unexpected spatial training chronology')
    return meta['profile_sha256']


def qualify_weather(root):
    """Run only input preparation. Source projects and archived results stay read-only."""
    remote = Path('E:/Roko/remote_access')
    if (socket.gethostname().lower() != 'pcsp-mxl0493vpr' or root.parent != remote
            or not root.name.startswith('zonal_2030_weather_')):
        raise ValueError('Run only from an isolated server input directory')
    sys.path.insert(0,str(remote/'energy_rebuild_20261019/runtime_packages'))
    import numpy as np
    import pandas as pd

    archive = remote/'final_frozen_profiles_20261031'
    national = remote/'national_production_03'
    project = national/'projects/average'
    def read(path):
        return json.loads(path.read_bytes())
    def pinned(path, expected, boundary):
        if not path.resolve().is_relative_to(boundary.resolve()) or digest(path) != expected:
            raise ValueError('Frozen input changed: '+str(path))
        return path
    preparation = read(national/'preparation.json')
    manifest = preparation['frozen_files']
    spatial = project/'spatial_sources.json'
    pinned(spatial,manifest[spatial.relative_to(national).as_posix()],project)
    stocks = capacities(root)
    sources, hashes = {}, {}
    for channel, (name, transfer_name, _, count) in SPATIAL.items():
        item = read(spatial)['channels'][channel]
        source, template, lineage = (project/item[k] for k in ('profile','transfer','lineage'))
        if (source.name,template.name) != (name,transfer_name):
            raise ValueError('Unexpected spatial source names')
        for path in (source,template,lineage):
            pinned(path,manifest[path.relative_to(national).as_posix()],project)
        source_sha = verify_training_source(source,lineage,channel)
        transfer = root/transfer_name
        write_transfer(template,transfer,stocks[channel],count)
        sources[channel] = source,transfer
        hashes[channel] = dict(training_sha256=source_sha,transfer_sha256=digest(transfer),
                               lineage_sha256=digest(lineage))

    jobs = read(archive/'jobspecs.json')
    archive_meta = read(archive/'preparation.json')
    donor_project = Path(jobs['electricity_avg']['project'])
    adapter_path = donor_project/'scripts/climate_newshare_adapter.py'
    adapter_sha = archive_meta['frozen_files'][str(adapter_path.relative_to(archive))]
    pinned(adapter_path,adapter_sha,donor_project)
    adapter_spec = importlib.util.spec_from_file_location('zonal_frozen_climate_adapter',adapter_path)
    module = importlib.util.module_from_spec(adapter_spec)
    sys.modules[adapter_spec.name] = module
    adapter_spec.loader.exec_module(module)
    results = {}
    units = [name for stock in stocks.values() for name in stock]
    native_columns = ['year','period',*units]
    cache = {}
    def remap(label, native_path, profile_path, expected_weather=None):
        profile = pd.read_csv(profile_path)
        if expected_weather is not None:
            reference = history.loc[(dates.year == expected_weather) & ~((dates.month==2)&(dates.day==29))]
            if len(reference)!=8760 or any(not np.allclose(profile[dst],reference[src],rtol=0,atol=1e-12)
                    for dst,src in (('solar_cf','solar'),('wind_cf','wind'),('temperature_c','temperature'))):
                raise ValueError('Historical weather differs from the pinned raw profile')
        if native_path not in cache:
            native = pd.read_csv(native_path,usecols=native_columns)
            cache[native_path] = native.loc[native['year'].eq(2030)].copy(),digest(native_path)
        native, native_sha = cache[native_path]
        output, receipt = install_curves(native,profile,sources,stocks,module._spatial_unit_profiles)
        dest = root/(label+'_ncre.csv')
        with dest.open('x',encoding='utf8',newline='') as f:
            output[native_columns].to_csv(f,index=False,float_format='%.17g')
        results[label] = dict(profile_sha256=digest(profile_path), source_ncre_sha256=native_sha,
                              mapped_ncre_sha256=digest(dest), spatial=receipt)

    for label in ('avg','low','high'):
        job = jobs['electricity_'+label]
        source_hashes = {Path(p): h for p,h in job['input_hashes'].items()}
        def check(path):
            path = Path(path)
            return pinned(path,source_hashes[path],archive)
        if job['ensemble_sha256'] != archive_meta['source_ensemble_sha256']:
            raise ValueError('Unexpected synthetic ensemble')
        spec = read(check(job['specification']))
        summary = read(check(spec['summary_path']))
        remap(label,check(spec['ncre_path']),check(summary['climate_profile_path']))

    historical = archive/'profiles/HR_data.parquet'
    hist_sha = 'b700263a2868b20cc8f7fe95e0a7d22c48ea31c89cedbc0889a55ca870972d8a'
    history = pd.read_parquet(pinned(historical,hist_sha,archive),columns=['time','solar','wind','temperature'])
    dates = pd.DatetimeIndex(history['time'])
    for year in range(2020,2025):
        source = archive/'stress'/f'avg_{year}_1p0'
        accepted = read(source/'accepted.json')
        audit = read(pinned(source/'audit.json',accepted['files']['audit.json'],source))
        if accepted['fence'] != archive_meta['fence'] or audit['historical_year'] != year or audit['hours'] != 8760:
            raise ValueError('Unexpected historical source')
        spec = read(source/'inputs'/str(year)/'avg_yield__normal/package_specification.json')
        summary = read(Path(spec['summary_path']))
        raw = Path(summary['climate_profile_path'])
        if not raw.resolve().is_relative_to(source.resolve()) or summary['historical_weather_year'] != year:
            raise ValueError('Unexpected historical profile scope')
        average = read(Path(jobs['electricity_avg']['specification']))
        ncre = Path(average['ncre_path'])
        pinned(ncre,{Path(p): h for p,h in jobs['electricity_avg']['input_hashes'].items()}[ncre],archive)
        remap(f'historical_{year}',ncre,raw,year)
    with (root/'weather_preflight.json').open('x',encoding='utf8') as f:
        json.dump(dict(status='input_preflight_only',no_optimization=True,source_spatial=hashes,
                       synthetic_ensemble_sha256=archive_meta['source_ensemble_sha256'],
                       historical_source_sha256=hist_sha,adapter_sha256=adapter_sha,
                       output_scope='2030 Croatian spatial columns only; overlay by (year, period)',
                       cases=results),f,indent=2,allow_nan=False)


if __name__ == '__main__':
    qualify_weather(Path(__file__).resolve().parent)

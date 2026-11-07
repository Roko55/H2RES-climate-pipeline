"""Frozen synthetic inputs underlying the V6.1 fixed 2030 electricity portfolios."""
import hashlib
import json
import pandas as pd
import numpy as np


def select_cases(root, design, year):
    if design != 'electricity' or year != 2030:
        raise ValueError('The V6.1 operating portfolios use the electricity design at 2030')
    folder = root/'evidence/calibrated'
    receipt = json.loads((folder/'electricity_profiles_sources.json').read_bytes())
    weather = json.loads((folder/'weather_preflight.json').read_bytes())
    cases = {}
    for row in receipt['sources']:
        name = row['case']
        path = (folder/row['file']).resolve()
        path.relative_to(folder.resolve())
        if (hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256'] or
                row['source_sha256'] != weather['cases'][name]['profile_sha256']):
            raise ValueError('Frozen electricity input identity differs: '+name)
        markers = list((folder/'zonal_2030_production_05/jobs'/('planning_'+name)).glob('attempt_*/accepted.json'))
        if len(markers) != 1 or json.loads(markers[0].read_bytes())['weather_overlay_sha256'] != row['mapped_ncre_sha256']:
            raise ValueError('Frozen profile differs from executed planning weather')
        frame = pd.read_csv(path, float_precision='round_trip')
        if (len(frame) != 8760 or not frame.year.eq(2030).all() or
                not np.array_equal(frame.period.to_numpy(), np.arange(1, 8761))):
            raise ValueError('Frozen electricity profile calendar differs')
        cases[name] = frame[['solar_cf', 'wind_cf', 'temperature_c']].to_numpy()
    if set(cases) != {'low', 'avg', 'high'}:
        raise ValueError('Missing electricity portfolio input')
    return cases

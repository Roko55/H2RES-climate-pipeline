"""Frozen single-channel sensitivity profiles for the full multisector model."""
import hashlib
import json
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from .release_runtime import accepted_array

YEARS = tuple(range(2020, 2051, 5))
DONORS = {'average': (65, 65, 65), 'solar_low': (2, 65, 65),
          'solar_high': (28, 65, 65), 'wind_low': (65, 20, 65),
          'wind_high': (65, 72, 65), 'temperature_cold': (65, 65, 41),
          'temperature_hot': (65, 65, 48)}


def channel_cases(root, year):
    if year not in YEARS:
        raise ValueError('Choose a five-year anchor from 2020 through 2050')
    ensemble = accepted_array(root)
    records = pd.read_csv(root/'results/channel_investments/scenario_inputs.csv')
    cases = {}
    for case, members in DONORS.items():
        profile = np.column_stack([ensemble[m, :, c] for c, m in enumerate(members)])
        if year > 2020:
            fraction = min((year - 2020) / (2050 - 2020), 1.)
            original = profile
            profile = original.copy()
            profile[:, 0] = np.clip(original[:, 0]*(1.+fraction*(1.03-1.)), 0, 1)
            profile[:, 1] = np.clip(original[:, 1]*(1.+fraction*(.92-1.)), 0, 1)
            profile[:, 2] = original[:, 2]+fraction*1.5
        expected = records[(records['case'] == case) & (records.year == year)]
        if len(expected) != 1 or hashlib.sha256(profile.tobytes()).hexdigest() != expected.iloc[0].profile_sha256:
            raise ValueError(f'Channel input identity differs: {case}, {year}')
        cases[case] = profile
    return cases


def verify_selection(root):
    """Recompute donor selection using only the frozen generated ensemble."""
    ensemble = accepted_array(root).astype(float)
    means = ensemble.mean(axis=1)

    def outer_members(values):
        model = KMeans(n_clusters=3, random_state=42, n_init=10).fit(values[:, None])
        groups = []
        for label in np.argsort(model.cluster_centers_[:, 0]):
            ids = np.flatnonzero(model.labels_ == label)
            groups.append((int(ids[np.argmin(abs(values[ids]-values[ids].mean()))]), ids))
        if 65 not in groups[1][1]:
            raise ValueError('Average donor is outside the central cluster')
        return groups[0][0], groups[2][0]

    temp = ensemble[:, :, 2]
    heating = np.maximum(18-temp, 0).sum(axis=1)
    cooling = np.maximum(temp-22, 0).sum(axis=1)
    thermal = (cooling-cooling.mean())/cooling.std()-(heating-heating.mean())/heating.std()
    selected = (*outer_members(means[:, 0]), *outer_members(means[:, 1]), *outer_members(thermal))
    if selected != (2, 28, 20, 72, 41, 48):
        raise ValueError('Frozen channel donors differ from the recorded selection')
    design = json.loads((root/'provenance/channel_investments/design.json').read_text())
    recorded = {r['id']: (r['solar_member'], r['wind_member'], r['temperature_member']) for r in design['cases']}
    if recorded != DONORS:
        raise ValueError('Channel design metadata differs')

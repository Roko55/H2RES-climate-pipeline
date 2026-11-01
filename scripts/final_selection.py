"""Final experiment designs, retaining each executed selector's arithmetic."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import numpy as np
from . import resource_selection, scenario_manager
from .release_runtime import accepted_array


def select_cases(root, design, year):
    if year not in range(2020, 2051, 5):
        raise ValueError('Choose a five-year planning anchor between 2020 and 2050')
    values = accepted_array(root)
    if design == 'electricity':
        records = resource_selection.get_representative_scenarios(values.astype(float), year)
        return {name: records[name+'_yield__normal']['profile'].copy() for name in ('low', 'avg', 'high')}
    if design != 'multisector':
        raise ValueError('Unknown experiment design')
    manager = scenario_manager
    previous_dir = manager.DATA_DIR
    previous_method = os.environ.get('H2RES_COMBINED_SCENARIO_METHOD')
    try:
        with tempfile.TemporaryDirectory() as folder:
            np.save(Path(folder)/'xgb_synthetic_scenarios.npy', values)
            manager.DATA_DIR = folder
            os.environ['H2RES_COMBINED_SCENARIO_METHOD'] = 'joint_medoid'
            records = manager.get_representative_scenarios(year)
    finally:
        manager.DATA_DIR = previous_dir
        if previous_method is None:
            os.environ.pop('H2RES_COMBINED_SCENARIO_METHOD', None)
        else:
            os.environ['H2RES_COMBINED_SCENARIO_METHOD'] = previous_method
    average = records['avg_yield__normal']['profile']
    profiles = {}
    expected = json.loads((root/'provenance/final/multisector_mapping_checks.json').read_text())
    for case in ('low', 'avg', 'high'):
        profile = records[case+'_yield__normal']['profile'].copy()
        profile[:, 2] = average[:, 2]
        record = next(r for r in expected['profiles'] if r['year'] == year and r['case'] == case)
        if hashlib.sha256(profile.tobytes()).hexdigest() != record['profile_sha256']:
            raise ValueError(f'Final multisector case differs: {case}, {year}')
        profiles[case] = profile
    return profiles

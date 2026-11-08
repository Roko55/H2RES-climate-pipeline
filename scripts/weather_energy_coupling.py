"""Drop-in, pinned thermal input adapter for an isolated future H2RES project."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=4)
def load_inputs(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root/'thermal_manifest.json').read_bytes())
    for name, expected in manifest['outputs_sha256'].items():
        path = (root/name).resolve()
        if path.parent != root or digest(path) != expected:
            raise ValueError('Thermal input changed: '+name)
    table = pd.read_csv(root/'thermal_by_member.csv.gz')
    tables = {}
    for (year, member), rows in table.groupby(['year','temperature_member'],sort=False):
        if not np.array_equal(rows.period.to_numpy(),np.arange(1,8761)):
            raise ValueError('Unexpected thermal calendar')
        tables[int(year),int(member)] = rows
    if len(tables) != 18:
        raise ValueError('Expected 18 temperature-member/year schedules')
    return manifest, tables


def apply_temperature_coupling_to_runtime(temp_profile, year, country_code, periods,
        heat_demand, cooling_demand, hp_cop_period, baseline_heat_demand,
        baseline_cooling_demand, baseline_hp_cop_period,
        district_heat_units=('TE-TOZagreb',), general_heat_units=('general_demand',),
        cooling_units=('TE-TOZagreb','general_cooling'), atw_cop_units=('ATW_HP',),
        data_directory=None, **legacy_options):
    """Replace future heat and ATW COP; household cooling uses its explicit power ledger."""
    if (str(country_code).upper() != 'HR' or tuple(periods) != tuple(range(1,8761))
            or int(year) not in tuple(range(2025,2051,5))
            or tuple(district_heat_units) != ('TE-TOZagreb',)
            or tuple(general_heat_units) != ('general_demand',)
            or tuple(atw_cop_units) != ('ATW_HP',)
            or tuple(cooling_units) != ('TE-TOZagreb','general_cooling')):
        raise ValueError('Unexpected thermal integration boundary')
    for key, expected in [('heating_base_c',18.),('cooling_base_c',22.),('demand_shape_blend',1.)]:
        if key in legacy_options and legacy_options[key] != expected:
            raise ValueError('Runtime thermal setting differs from the frozen preparation: '+key)
    if legacy_options.get('scale_annual_totals') is False:
        raise ValueError('Old constant-annual-energy flag is still active')
    root = Path(data_directory) if data_directory is not None else Path(__file__).resolve().parents[1]/'data/weather_energy'
    manifest, tables = load_inputs(str(root.resolve()))
    temp = np.asarray(temp_profile,dtype=float)
    if temp.shape != (8760,) or not np.isfinite(temp).all():
        raise ValueError('Invalid runtime temperature')
    matches = [(member,rows) for (y,member),rows in tables.items() if y==int(year)
               and np.allclose(temp,rows.temperature_c.to_numpy(),atol=1e-10,rtol=0)]
    if len(matches) != 1:
        raise ValueError('Runtime temperature is not the archived selected member')
    member, data = matches[0]
    diagnostic = dict(year=int(year),temperature_member=member,scale_annual_totals=True,
        temp_mean_c=float(temp.mean()),temp_min_c=float(temp.min()),temp_max_c=float(temp.max()),
        reference_year=2020,thermal_design='weather_energy_v1',cop_units_updated=['ATW_HP'],
        district_heat_units_updated=['TE-TOZagreb'],general_heat_units_updated=['general_demand'],
        cooling_units_updated=['TE-TOZagreb','general_cooling'],
        household_cooling='Pinned, exogenous electrical cooling at COP3 in the separate checked power ledger',
        legacy_elasticities_used=False,cop_mean_renormalized_per_case=False,
        district_heat_scale_factors={},general_heat_scale_factors={},cooling_scale_factors={})
    for unit,prefix,section in [('general_demand','general','general'),('TE-TOZagreb','district','district')]:
        base = [baseline_heat_demand[(unit,p,int(year))] for p in periods]
        if abs(sum(base)-manifest['annual_targets'][str(int(year))][unit])>.01:
            raise ValueError('Native heat annual boundary differs from calibrated forecast: '+unit)
        values = (data[prefix+'_space_mwh']+data[prefix+'_water_mwh']).to_numpy()
        for p,value in zip(periods,values):
            key = unit,p,int(year)
            if key not in heat_demand:
                raise ValueError('Missing native heat key')
            heat_demand[key] = float(value)
        diagnostic[section+'_heat_scale_factors'][unit] = float(values.sum()/sum(base))
    for unit in cooling_units:
        # The accepted national setup has no endogenous district cooling. Do not silently erase one.
        for p in periods:
            key = unit,p,int(year)
            if key not in baseline_cooling_demand or (unit == 'TE-TOZagreb' and baseline_cooling_demand[key] != 0.):
                raise ValueError('New endogenous cooling requires its own weather-and-meter design')
            cooling_demand[key] = 0.
    for p,cop in zip(periods,data.atw_cop):
        key = 'ATW_HP',p,int(year)
        if key not in baseline_hp_cop_period or key not in hp_cop_period:
            raise ValueError('Missing native air-source COP key')
        hp_cop_period[key] = float(cop)
    return diagnostic

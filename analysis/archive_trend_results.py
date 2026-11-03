"""Verify and summarize the archived single-channel multisector results."""
from pathlib import Path
import argparse
import json
import os
import sys

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
os.environ.setdefault('MPLBACKEND', 'Agg')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scripts.trend_channel_profiles import DONORS, YEARS, channel_cases, verify_selection


def require(condition, message):
    if not condition:
        raise ValueError(message)


def tables(root):
    folder = root/'results/channel_investments'
    return {name: pd.read_csv(folder/(name+'.csv')) for name in (
        'scenario_inputs', 'objective_comparison', 'objective_components',
        'investments_by_technology', 'installed_stock', 'channel_comparison',
        'system_outcomes', 'thermal_inputs')}


def verify_results(root=ROOT):
    data = tables(root)
    keys = {(case, year) for case in DONORS for year in YEARS}
    objective = data['objective_comparison']
    require(len(objective) == 49 and set(zip(objective['case'], objective.year)) == keys,
            'Incomplete or duplicate planning windows')
    require(np.isfinite(objective.objective).all() and
            (objective.constraint_violation <= 1e-7).all() and
            (objective.bound_violation <= 1e-7).all(), 'Solver numerical acceptance failed')
    reference = objective[objective['case'] == 'average'].set_index('year')
    expected_delta = objective.objective-objective.year.map(reference.objective)
    np.testing.assert_allclose(objective.delta_vs_average, expected_delta, rtol=0, atol=1e-6)
    np.testing.assert_allclose(objective.percent_vs_absolute_average,
        100*expected_delta/objective.year.map(reference.objective).abs(), rtol=0, atol=1e-10)
    for row in objective.itertuples():
        require(json.loads(row.window_years) == ([row.year, row.year+5] if row.year < 2050 else [2050]),
                'Native window definition differs')
    previous = pd.read_csv(root/'results/planning_objectives.csv')
    previous = previous[(previous.configuration == 'multisector') & (previous['case'] == 'avg')].set_index('year')
    np.testing.assert_array_equal(reference.objective, previous.loc[reference.index, 'objective'])
    require((reference.source_sha256 == previous.loc[reference.index, 'source_sha256']).all(),
            'Reused Average source identities differ')
    components = data['objective_components']
    totals = components[components.active_in_objective == 1].groupby(['case', 'window_start']).value.sum()
    expected = objective.set_index(['case', 'year']).objective.reindex(totals.index)
    require(len(totals) == 49 and expected.notna().all(), 'Missing objective components')
    require(np.max(np.abs(totals.to_numpy()-expected.to_numpy())) < .01,
            'Objective decomposition does not close')

    investments = data['investments_by_technology']
    require((investments.realized == 1).all() and
            (investments.decision_year == investments.window_start).all(),
            'Provisional decisions included as realized investment')
    require(not investments.duplicated(['case', 'family', 'key_json']).any(),
            'Duplicate realized investment')
    np.testing.assert_allclose(investments.objective_contribution,
        investments.value*investments.objective_coefficient, rtol=1e-12, atol=1e-6)
    is_storage = investments.family.isin(['stat_sto_cap_inv', 'h2_sto_max_cap'])
    require((investments.unit == np.where(is_storage, 'MWh', 'MW')).all(), 'Mixed capacity units')
    require((investments.capacity_addition == (investments.family != 'extra_cap_inv').astype(int)).all(),
            'Capacity-addition flags differ')
    baseline = investments[investments['case'] == 'average'].set_index(['family', 'key_json'])
    pairs = pd.MultiIndex.from_frame(investments[['family', 'key_json']])
    comparison = data['channel_comparison']
    key = ['case', 'family', 'key_json']
    comparison = comparison.set_index(key).loc[pd.MultiIndex.from_frame(investments[key])]
    np.testing.assert_allclose(comparison.capacity_delta_vs_average,
        investments.value.to_numpy()-baseline.value.reindex(pairs).to_numpy(), rtol=0, atol=1e-8)
    np.testing.assert_allclose(comparison.investment_objective_delta,
        investments.objective_contribution.to_numpy()-baseline.objective_contribution.reindex(pairs).to_numpy(),
        rtol=0, atol=1e-6)
    stock = data['installed_stock']
    require(len(stock) == 2009, 'Stock record dimensions differ')
    np.testing.assert_allclose(stock.installed_native_capacity,
        stock.remaining_original+stock.inherited_new+stock.new_addition, rtol=0, atol=1e-6)
    evidence = root/'provenance/channel_investments'
    integrity = json.loads((evidence/'final_integrity.json').read_text())
    require(integrity['all_passed'] and integrity['planning_windows'] == 49 and
        integrity['new_planning_windows'] == 42 and integrity['reused_windows'] == 7 and
        integrity['optimal'] == 49 and integrity['new_historical_stress_tests'] == 0,
        'Completed channel integrity differs')
    require(len(integrity['proofs']) == 49 and sum(r['reused'] for r in integrity['proofs']) == 7,
            'Average reuse proof count differs')
    reuse = json.loads((evidence/'reference_decision.json').read_text())
    require(reuse['reuse'] and len(reuse['audits']) == 7 and all(
        r['reuse_proved'] and r['input_and_state_equal'] and not r['new_solve_started']
        for r in reuse['audits']), 'Average reuse input/state proof failed')
    audit = json.loads((evidence/'post_audit.json').read_text())
    for name, size in [('stock_checks', 2009), ('emission_checks', 273), ('balance_checks', 49)]:
        require(len(audit[name]) == size and all(r['passed'] for r in audit[name]), name+' failed')
    mapping = json.loads((evidence/'mapping_checks.json').read_text())
    require(len(mapping) == 98 and max(r['max_error'] for r in mapping) <= 1e-12,
            'Channel spatial conservation failed')
    verify_selection(root)
    inputs = data['scenario_inputs']
    require(len(inputs) == 49 and set(zip(inputs['case'], inputs.year)) == keys,
            'Channel input table dimensions differ')
    for year in YEARS:
        profiles = channel_cases(root, year)
        for case, profile in profiles.items():
            row = data['scenario_inputs'].query('case == @case and year == @year').iloc[0]
            np.testing.assert_allclose([row.mean_solar_CF, row.mean_wind_CF, row.mean_temperature_C],
                                      profile.astype(float).mean(axis=0), rtol=0, atol=1e-12)
            np.testing.assert_allclose([row.heating_degree_hours_18C, row.cooling_degree_hours_22C],
                [np.maximum(18-profile[:, 2], 0).sum(), np.maximum(profile[:, 2]-22, 0).sum()], rtol=0, atol=1e-8)
            require((row.solar_member, row.wind_member, row.temperature_member) == DONORS[case],
                    'Input table donor IDs differ')
            changed = [c for c in range(3) if not np.array_equal(profile[:, c], profiles['average'][:, c])]
            expected_changed = [] if case == 'average' else [0 if case.startswith('solar') else 1 if case.startswith('wind') else 2]
            require(changed == expected_changed, 'An unchanged channel differs')
    return {'planning_windows': 49, 'new_windows': 42, 'reused_windows': 7,
            'stock_checks': 2009, 'emission_checks': 273, 'balance_checks': 49,
            'input_profile_hashes': 49, 'new_operational_tests': 0}


def summaries(root=ROOT):
    data = tables(root)
    objective = data['objective_comparison'].drop(columns=['source', 'source_sha256'])
    inputs = data['scenario_inputs'].drop(columns=['profile_sha256'])
    outcomes = objective.merge(inputs, on=['case', 'year'], validate='one_to_one').sort_values(['case', 'year'])
    inv = data['investments_by_technology']
    inv = inv[inv.capacity_addition == 1]
    capacity = inv.groupby(['case', 'decision_year', 'family', 'unit'], as_index=False).agg(
        new_capacity=('value', 'sum'), native_objective_contribution=('objective_contribution', 'sum'))
    capacity = capacity.rename(columns={'decision_year': 'year'})
    average = capacity[capacity['case'] == 'average'].drop(columns='case')
    capacity = capacity.merge(average, on=['year', 'family', 'unit'], suffixes=('', '_average'), validate='many_to_one')
    capacity['new_capacity_delta_vs_average'] = capacity.new_capacity-capacity.new_capacity_average
    totals = capacity.groupby(['case', 'year', 'unit'], as_index=False).new_capacity_delta_vs_average.sum()
    return {'objective_summary.csv': outcomes, 'investment_summary.csv': capacity,
            'capacity_changes.csv': totals}


def plot(summaries, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    groups = [('Solar', 'solar_low', 'solar_high'), ('Wind', 'wind_low', 'wind_high'),
              ('Temperature', 'temperature_cold', 'temperature_hot')]
    for unit in ('MW', 'MWh', 'objective'):
        fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.4), constrained_layout=True)
        for ax, (title, low, high) in zip(axes, groups):
            for case, color, label in zip((low, high), ('#167D9A', '#D47A39'),
                    ('Cold', 'Hot') if title == 'Temperature' else ('Low', 'High')):
                if unit == 'objective':
                    frame = summaries['objective_summary.csv'].query('case == @case').sort_values('year')
                    y = frame.percent_vs_absolute_average
                else:
                    frame = summaries['capacity_changes.csv'].query('case == @case and unit == @unit').sort_values('year')
                    y = frame.new_capacity_delta_vs_average
                ax.plot(frame.year, y, marker='o', ms=4, lw=1.8, color=color, label=label)
            ax.axhline(0, color='#555555', lw=.8)
            ax.set(title=title, xlabel='Planning anchor', xticks=YEARS[::2])
            ax.spines[['top', 'right']].set_visible(False)
            ax.grid(axis='y', alpha=.18)
            ax.legend(frameon=False)
        ylabel = 'Window objective difference / |Average| (%)' if unit == 'objective' else (
            'Realized storage additions minus Average (MWh)' if unit == 'MWh' else
            'Sum of realized capacity additions minus Average (MW)')
        axes[0].set_ylabel(ylabel)
        name = 'objective_changes' if unit == 'objective' else 'investment_changes_'+unit
        fig.savefig(output/(name+'.png'), dpi=180)
        fig.savefig(output/(name+'.pdf'), metadata={'CreationDate': None, 'ModDate': None})
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New directory under exports/')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.relative_to((ROOT/'exports').resolve())
    require(not output.exists(), 'Choose a new output directory')
    result = verify_results()
    frames = summaries()
    output.mkdir(parents=True)
    for name, frame in frames.items():
        frame.to_csv(output/name, index=False)
    plot(frames, output)
    (output/'verification.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

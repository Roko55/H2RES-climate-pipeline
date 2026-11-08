"""Reproduce the numerical tables supporting V6.2 sections 4.1 through 4.6."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys

for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'
os.environ.setdefault('MPLBACKEND', 'Agg')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from analysis.channel_results import tables, verify_results


def technology(row):
    unit = json.loads(row.key_json)[0]
    if row.family == 'cap_inv':
        return ('Solar PV' if 'Solar' in unit else 'Wind' if 'Wind' in unit else
                {'NEKrskoHR': 'Nuclear', 'TE-TOZagreb': 'CHP'}.get(unit, unit))
    if row.family == 'trad_boiler_ind_cap_inv':
        return {'gas_boilers': 'Individual gas boiler', 'bio_boilers': 'Individual biomass boiler',
                'oil_boilers': 'Individual oil boiler'}.get(unit, unit)
    if row.family == 'trad_boiler_dh_cap_inv':
        return 'District gas boiler' if unit == 'gas_boilers' else unit
    return {'ATW_HP': 'Air-to-water HP', 'geothermal_HP': 'Geothermal HP',
            'electric_boiler': 'Electric boiler'}.get(unit, unit)


def summaries(root=ROOT):
    data = tables(root)
    metrics = pd.read_csv(root/'results/profile_metrics.csv')
    comparison = []
    for channel, group in metrics.groupby('channel', sort=False):
        errors = group.set_index('method').MAE
        generated = errors['ensemble_mean']
        for key, label in [('fixed_H2RES_profile', 'Default H2RES profile'),
                           ('training_calendar_mean', 'Historical mean')]:
            comparison.append({'channel': channel, 'reference': label,
                'reference_MAE': errors[key], 'generated_MAE': generated,
                'change_percent': 100*(generated/errors[key]-1)})

    objective = data['objective_comparison']
    window = objective[['case', 'year', 'window_years']].copy()
    window['difference_million_EUR'] = objective.delta_vs_average/1e6
    components = data['objective_components']
    active = components[components.active_in_objective == 1]
    groups = active.groupby(['case', 'window_start', 'component_group'], as_index=False).value.sum()
    average = groups[groups['case'] == 'average'].drop(columns='case')
    groups = groups.merge(average, on=['window_start', 'component_group'],
                          suffixes=('', '_average'), validate='many_to_one')
    groups['difference_million_EUR'] = (groups.value-groups.value_average)/1e6
    groups = groups[['case', 'window_start', 'component_group', 'difference_million_EUR']]

    from analysis.native_results import annual_costs
    investments = data['investments_by_technology']
    annual = annual_costs(root)

    capacity = investments[(investments['case'] == 'average') &
        (investments.capacity_addition == 1) & (investments.unit == 'MW')].copy()
    capacity['technology'] = capacity.apply(technology, axis=1)
    capacity = capacity.groupby(['decision_year', 'technology', 'unit'], as_index=False).value.sum()
    capacity = capacity.rename(columns={'decision_year': 'year', 'value': 'new_capacity'})

    outcomes = data['system_outcomes']
    average = outcomes[outcomes['case'] == 'average']
    supply = []
    for row in average.itertuples(index=False):
        unit = json.loads(row.key_json)[0]
        scope = None
        if row.family == 'generation' and unit == 'NEKrskoHR':
            scope, name = 'Electricity', 'Net imports including Krško entitlement'
        elif row.family in ('generation', 'generation_FC'):
            scope = 'Electricity'
            name = ('Solar PV' if 'Solar' in str(unit) else 'Wind' if 'Wind' in str(unit) else
                    'Hydro' if str(unit).startswith(('HE', 'RHE')) or unit in ('hror', 'HRSmallHydro2020') else
                    'Other domestic generation')
        elif row.family == 'imports':
            scope, name = 'Electricity', 'Net imports including Krško entitlement'
        elif row.family == 'boiler_ind_generation':
            scope = 'Heat'
            name = {'gas_boilers': 'Individual gas boiler', 'bio_boilers': 'Individual biomass boiler',
                    'oil_boilers': 'Individual oil boiler'}[unit]
        elif row.family in ('heat_pump_out', 'heat_gen'):
            scope, name = 'Heat', 'District heat and heat pumps'
        if scope:
            supply.append({'year': row.year, 'scope': scope, 'technology': name, 'TWh': row.value/1e6})
    supply = pd.DataFrame(supply).groupby(['year', 'scope', 'technology'], as_index=False).TWh.sum()
    emissions = average[average.family.isin(['CO2_total', 'CO2_total_heat', 'CO2_total_industry'])]
    emissions = emissions.pivot(index='year', columns='family', values='value').reset_index()
    emissions.columns.name = None
    emissions = emissions.rename(columns={'CO2_total': 'electricity_Mt', 'CO2_total_heat': 'heat_Mt',
                                         'CO2_total_industry': 'industry_Mt'})
    for key in ('electricity_Mt', 'heat_Mt', 'industry_Mt'):
        emissions[key] /= 1e6
    operations = pd.read_csv(root/'results/operational_checks.csv')
    operations = operations[['portfolio', 'historical_year', 'availability_fraction',
        'HR_unserved_MWh', 'HR_CEEP_MWh']].copy()
    operations['HR_CEEP_TWh'] = operations.pop('HR_CEEP_MWh')/1e6
    crps = pd.read_csv(root/'results/manuscript/fair_crps.csv')
    crps['fair_CRPS_change_percent'] = 100*(crps.synthetic_fair_CRPS/crps.historical_fair_CRPS-1)
    return {'mean_profile_MAE.csv': pd.DataFrame(comparison), 'fair_crps.csv': crps,
            'window_cost_differences.csv': window,
            'cost_component_differences.csv': groups, 'realized_annual_costs.csv': annual,
            'average_capacity.csv': capacity, 'average_energy_supply.csv': supply,
            'average_direct_emissions.csv': emissions, 'operational_surplus.csv': operations}


def verify_manuscript(root=ROOT):
    scope = json.loads((root/'provenance/manuscript/scope.json').read_text())
    expected = {str(i) for i in range(1, 15)} | {'15a', '15b', '16'}
    if {r['figure'] for r in scope['figures']} != expected:
        raise ValueError('V6.2 figure inventory differs')
    for row in scope['figures']:
        path = (root/row['file']).resolve()
        path.relative_to(root.resolve())
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('V6.2 figure differs: '+row['figure'])
    metrics = pd.read_csv(root/'results/profile_metrics.csv')
    if (len(metrics) != 9 or set(metrics.method) !=
        {'fixed_H2RES_profile', 'ensemble_mean', 'training_calendar_mean'} or
        set(metrics.n_hours) != {43800} or set(metrics['mask']) != {'complete_reporting'}):
        raise ValueError('V6 mean-profile comparison scope differs')
    crps = pd.read_csv(root/'results/manuscript/fair_crps.csv')
    if len(crps) != 3 or set(crps.channel) != {'solar', 'wind', 'temperature'}:
        raise ValueError('V6 fair CRPS comparison scope differs')
    np.testing.assert_allclose(crps.fair_CRPS_change_percent,
        100*(crps.synthetic_fair_CRPS/crps.historical_fair_CRPS-1), rtol=0, atol=1e-10)
    if not (crps.n_reporting_hours.eq(43800).all() and crps.historical_members.eq(36).all()
            and crps.synthetic_members.eq(100).all()):
        raise ValueError('Different calendar or ensemble size in fair CRPS')
    return {'manuscript_sha256': scope['manuscript_sha256'], 'exact_manuscript_figures': 17,
            'mean_profile_comparisons': 6, 'fair_CRPS_comparisons': 3}


def plot(frames, output):
    """Plot the reported mean-error and distribution comparisons from their tables."""
    import matplotlib.pyplot as plt
    frame = frames['mean_profile_MAE.csv']
    names = ['solar', 'wind', 'temperature']
    fig, ax = plt.subplots(figsize=(8.5, 3.))
    for shift, reference, color in [(-.14, 'Default H2RES profile', '#0284c7'),
                                     (.14, 'Historical mean', '#7c3aed')]:
        selected = frame[frame.reference == reference].set_index('channel').loc[names]
        y = np.arange(3)+shift
        ax.hlines(y, 0, selected.change_percent, color=color)
        ax.scatter(selected.change_percent, y, color=color, label=reference)
        for value, yy in zip(selected.change_percent, y):
            ax.annotate(f'{value:+.2f}%', (value, yy), xytext=(-5 if value < 0 else 5, 0),
                textcoords='offset points', va='center', ha='right' if value < 0 else 'left', color=color)
    ax.set(yticks=range(3), yticklabels=['Solar', 'Wind', 'Temperature'], xlim=(-68, 14),
           xlabel='Change in mean-profile MAE (%)')
    ax.invert_yaxis()
    ax.axvline(0, color='#64748b')
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.02), ncol=2, frameon=False)
    fig.tight_layout()
    fig.savefig(output/'mean_profile_MAE.png', dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New directory under exports/')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.relative_to((ROOT/'exports').resolve())
    if output.exists():
        raise ValueError('Choose a new output directory')
    report = verify_results(ROOT)
    report.update(verify_manuscript(ROOT))
    frames = summaries(ROOT)
    output.mkdir(parents=True)
    for name, frame in frames.items():
        frame.to_csv(output/name, index=False)
    plot(frames, output)
    (output/'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

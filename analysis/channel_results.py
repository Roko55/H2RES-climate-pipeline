"""Verify the calibrated V6.2 stationary-weather experiment."""
from pathlib import Path
import os
import sys
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
os.environ.setdefault('MPLBACKEND', 'Agg')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from analysis.native_results import national_tables, verify_national, annual_costs
from scripts.channel_profiles import verify_selection


def tables(root):
    return {name: pd.read_csv(root/'results/channel_sensitivity'/(name+'.csv')) for name in (
        'scenario_inputs', 'objective_comparison', 'objective_components',
        'investments_by_technology', 'system_outcomes', 'thermal_inputs')}


def verify_results(root=ROOT):
    data = tables(root)
    inputs = data['scenario_inputs']
    if not inputs.groupby('case').profile_sha256.nunique().eq(1).all():
        raise ValueError('Weather profile changes across planning anchors')
    inv = data['investments_by_technology']
    if not (inv.realized.eq(1).all() and inv.decision_year.eq(inv.window_start).all()):
        raise ValueError('Provisional decisions included as realized investment')
    report = verify_national(root)
    verify_selection(root)
    for name, expected in national_tables(root).items():
        pd.testing.assert_frame_equal(data[name], expected.reset_index(drop=True),
            check_dtype=False, rtol=1e-12, atol=1e-8, obj=name)
    costs = pd.read_csv(root/'results/channel_sensitivity/realized_annual_costs.csv')
    pd.testing.assert_frame_equal(costs, annual_costs(root), check_dtype=False, rtol=1e-12, atol=1e-8)
    return report


def main():
    from analysis.manuscript_results import main as reproduce
    reproduce()


if __name__ == '__main__':
    main()

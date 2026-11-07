"""Verify the inactive dedicated hydrogen scope of the stationary planning design."""
from pathlib import Path

from electricity_project_integration import digest_file


DEMAND = 'data/SC/demand_H2_2020_2050_sdewes_NONE.csv'
DEMAND_SHA256 = '5d44b4d752078895b69340afc2430bd293fd449f2e3ad55145f4f79dcc5735e3'
FAMILIES = (
    ('FC_units','FC_init_cap','FC_max_cap','cap_inv_FC','max_FC_inv_cap'),
    ('Elec_h2_units','H2_init_cap','H2_max_cap','h2_electrolysis_max_cap','max_Elec_h2_inv_cap'),
    ('H2_storage_unit','H2_sto_init','H2_sto_max_cap','h2_sto_max_cap','max_H2_storage_inv_cap'),
)
OPERATIONS = ('generation_FC','generation_H2','power_to_h2','H2tInd','h2_sto_soc','h2_sto_out')
TOL = 1e-7


def prepare_hydrogen_scope(ctx, project, frozen):
    root = Path(project).resolve()
    path = (root/ctx['h2_demand_dat']).resolve()
    if path != root/DEMAND or frozen.get(DEMAND) != DEMAND_SHA256 or digest_file(path) != DEMAND_SHA256:
        raise ValueError('Planning hydrogen end use must use the frozen zero-demand file')
    if any(value != 0. for value in ctx['H2_demand'].values()):
        raise ValueError('The stationary study has no dedicated hydrogen end use')
    for dims, initial, ceiling, _, _ in FAMILIES[:-1]:
        if any(ctx[name][unit] != 0. for name in (initial,ceiling) for unit in ctx[dims]):
            raise ValueError('Dedicated fuel cell or electrolyser capacity differs from the study scope')
    unit = 'H2_storage_tank'
    if (tuple(ctx['H2_storage_unit']) != (unit,) or ctx['H2_sto_init'][unit] not in (0.,10.)
            or ctx['H2_sto_max_cap'][unit] not in (0.,100.)):
        raise ValueError('Unexpected unused hydrogen tank configuration')
    ctx['H2_sto_init'][unit] = 0.
    ctx['H2_sto_max_cap'][unit] = 0.


def audit_built_hydrogen_scope(ctx):
    model = ctx['ucp']
    model.update()
    counts = {}
    for dim, _, _, family, constraint in FAMILIES:
        count = 0
        for unit in ctx[dim]:
            for year in ctx['years']:
                row, addition = ctx[constraint][unit,year], ctx[family][unit,year]
                if (row.Sense != '<' or row.RHS != 0. or model.getRow(row).size() != 1
                        or model.getCoeff(row,addition) != 1. or addition.LB != 0.):
                    raise ValueError(f'Dedicated hydrogen capacity can expand in {unit}/{year}')
                count += 1
        counts[family] = count
    return dict(dedicated_end_use_mwh=0., existing_storage_mwh=0., zero_investment_rows=counts)


def audit_solved_hydrogen_scope(ctx):
    if ctx['ucp'].Status != 2:
        raise ValueError('Expected optimal solution before hydrogen accounting')
    peaks = {family:max(abs(var.X) for var in ctx[family].values()) for family in OPERATIONS}
    if not all(value <= TOL for value in peaks.values()):
        raise ValueError('Unexpected dedicated hydrogen operation')
    return dict(maximum_operation_mwh=peaks)

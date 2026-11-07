"""Verify the actual Croatian cut in the electricity-only stress model."""
from math import isfinite

from electricity_krsko_preflight import near
from electricity_window_preflight import HOURS


def audit_stress_exchange(ctx, model, fraction, solved=False):
    """Check both directions before solving and actual flow when solved."""
    if (fraction not in (0., .5, 1.) or tuple(ctx['years']) != (2030,)
            or tuple(ctx['periods']) != HOURS or ctx['include_secc_links']
            or ctx['model_efuel'] or ctx['model_H2_transmission']):
        raise ValueError('Expected a full-hour electricity stress window with primary links only')
    if not any(system == 'HR' for _, system in ctx['active_zones_system_df_list']):
        raise ValueError('No Croatian zone in the stress model')
    year, = ctx['years']
    model.update()
    summary = dict(hr_primary_rows=0, hr_outside_rows=0, other_rows=0,
                   max_hr_link_flow_mwh=0. if solved else None)
    families = (
        ('primary_links', 'import_zone_system', 'max_transfer_systexpimp_pos'),
        ('primary_links_export', 'export_zone_system', 'max_transfer_systexpimp_neg'),
    )
    for links, flows, constraints in families:
        before = summary['hr_primary_rows']
        for unit, zone, system, other_zone, other_system in ctx[links]:
            if unit not in ctx['transmission_units_el']:
                continue
            crosses = (system == 'HR') != (other_system == 'HR')
            cap = ctx['transfer_cap_hour_prim'][unit, year, zone, system, other_zone, other_system]
            for period in HOURS:
                flow = ctx[flows][unit, year, period, zone, system, other_zone, other_system]
                target = fraction if crosses else 1.
                row = find_flow_row(model, ctx[constraints],
                                    (unit, zone, system, other_zone, other_system, year, period),
                                    flow, cap, target)
                check_flow(model, row, flow, cap, target, solved)
                summary['hr_primary_rows' if crosses else 'other_rows'] += 1
                if crosses and solved:
                    summary['max_hr_link_flow_mwh'] = max(summary['max_hr_link_flow_mwh'], abs(flow.X))
        if summary['hr_primary_rows'] == before:
            raise ValueError(f'Missing a Croatian primary direction in {constraints}')

    for direction, row_name in (('import', 'max_transfer_systimpexp_outsideup'),
                                ('export', 'max_transfer_systimpexp_outsidedown')):
        flows = ctx[f'{direction}_outside']
        constraints = ctx[row_name]
        before = summary['hr_outside_rows']
        for unit, zone, system in ctx['outside_links']:
            if unit not in ctx['transmission_outside_units_el']:
                continue
            crosses = system == 'HR'
            cap = ctx['transfer_cap_outside'][unit, year, zone, system]
            for period in HOURS:
                flow = flows[unit, year, period, zone, system]
                target = fraction if crosses else 1.
                row = find_flow_row(model, constraints, (unit, zone, system, year, period),
                                    flow, cap, target)
                check_flow(model, row, flow, cap, target, solved)
                summary['hr_outside_rows' if crosses else 'other_rows'] += 1
                if crosses and solved:
                    summary['max_hr_link_flow_mwh'] = max(summary['max_hr_link_flow_mwh'], abs(flow.X))
        if summary['hr_outside_rows'] == before:
            raise ValueError(f'Missing a Croatian outside {direction} path')
    return summary


def find_flow_row(model, rows, key, flow, cap, fraction):
    row = rows.get(key)
    if row is not None:
        return row
    column = model.getCol(flow)
    for i in range(column.size()):
        row = column.getConstr(i)
        if (row.Sense == '<' and near(row.RHS, 0.) and
                model.getRow(row).size() == (2 if fraction else 1) and
                model.getCoeff(row, flow) == 1. and model.getCoeff(row, cap) == -fraction):
            return row
    raise ValueError(f'Missing native transfer bound for {key}')


def check_flow(model, constraint, flow, cap, fraction, solved):
    row = model.getRow(constraint)
    expected = {flow.index: 1.}
    if fraction:
        expected[cap.index] = -fraction
    actual = {row.getVar(i).index: row.getCoeff(i) for i in range(row.size())}
    if (constraint.Sense != '<' or not near(constraint.RHS, 0.) or actual != expected):
        raise ValueError(f'Incorrect stress exchange bound for {flow.VarName}')
    if solved and (not all(isfinite(v) for v in (flow.X, cap.X))
                   or cap.X < -1e-7 or flow.X < -1e-7 or flow.X > fraction * cap.X + 1e-7):
        raise ValueError(f'Solved flow exceeds its stress bound for {flow.VarName}')

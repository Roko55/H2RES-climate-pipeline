"""Physical input checks for a fixed future EV and cooling schedule."""
from math import fsum, isfinite


HOURS = 8760
HOURLY_TOL = 1e-7
ANNUAL_TOL = .01


def check_demand_window(series, limits):
    """Audit one year; ev_driving is battery-side, ev_charge is grid-side."""
    fields = ('generic_power','residual_power','ev_reference','ev_charge','ev_driving','ev_available',
              'cooling_reference','cooling_electric','cooling_useful','cooling_cop')
    if set(series) != set(fields) or any(len(series[name]) != HOURS for name in fields):
        raise ValueError('Future demand requires a full 8,760-hour input for every channel')
    if any(not isfinite(v) or v < 0 for name in fields for v in series[name]):
        raise ValueError('Nonfinite or negative future demand input')
    required = ('ev_efficiency','ev_connection_mw','ev_usable_storage_mwh','ev_initial_mwh',
                'cooling_connection_mw','cooling_service_mw','cooling_annual_useful_mwh','power_peak_mw')
    if set(limits) != set(required) or any(not isfinite(limits[name]) or limits[name] < 0 for name in required):
        raise ValueError('Independent physical limits are required; do not infer them from these schedules')
    efficiency = limits['ev_efficiency']
    stock = limits['ev_initial_mwh']
    capacity = limits['ev_usable_storage_mwh']
    if not 0 < efficiency <= 1 or stock > capacity:
        raise ValueError('Invalid EV efficiency or initial usable storage')
    peaks = dict(ev_charge=0., cooling_electric=0., cooling_useful=0., combined_power=0.)
    lowest = highest = stock
    reference_error = 0.
    deltas = []
    for i,values in enumerate(zip(*(series[name] for name in fields)),1):
        generic,residual,ev_ref,charge,drive,available,cool_ref,cool,useful,cop = values
        reference_error = max(reference_error,abs(fsum((generic,-residual,-ev_ref,-cool_ref))))
        if reference_error > HOURLY_TOL:
            raise ValueError(f'Embedded reference power was not subtracted exactly once in hour {i}')
        if abs(charge-ev_ref) > HOURLY_TOL:
            raise ValueError(f'Fixed EV add-back differs from the common reference in hour {i}')
        if available > 1 or charge > available*limits['ev_connection_mw']+HOURLY_TOL:
            raise ValueError(f'EV charging exceeds available connection in hour {i}')
        stock = fsum((stock,efficiency*charge,-drive))
        if not -HOURLY_TOL <= stock <= capacity+HOURLY_TOL:
            raise ValueError(f'EV storage leaves usable bounds in hour {i}')
        lowest,highest = min(lowest,stock),max(highest,stock)
        if cop <= 0 or abs(useful-cool*cop) > HOURLY_TOL:
            raise ValueError(f'Cooling service and electric input do not agree in hour {i}')
        if cool > limits['cooling_connection_mw']+HOURLY_TOL or useful > limits['cooling_service_mw']+HOURLY_TOL:
            raise ValueError(f'Cooling exceeds physical capacity in hour {i}')
        combined = fsum((residual,charge,cool))
        if combined > limits['power_peak_mw']+HOURLY_TOL:
            raise ValueError(f'Combined exogenous electric demand exceeds the independent peak bound in hour {i}')
        for key,value in zip(peaks,(charge,cool,useful,combined)):
            peaks[key] = max(peaks[key],value)
        deltas.append(combined-generic)
    annual_useful = fsum(series['cooling_useful'])
    if abs(stock-limits['ev_initial_mwh']) > HOURLY_TOL:
        raise ValueError('Fixed EV schedule does not return to the initial usable storage')
    if abs(annual_useful-limits['cooling_annual_useful_mwh']) > ANNUAL_TOL:
        raise ValueError('Cooling annual service does not match the independent target')
    return dict(status='input_physical_checks_passed',hours=HOURS,peaks_mw=peaks,
                cooling_annual_useful_mwh=annual_useful,ev_grid_mwh=fsum(series['ev_charge']),
                ev_terminal_mwh=stock,ev_min_usable_mwh=lowest,ev_max_usable_mwh=highest,
                max_reference_closure_error_mwh=reference_error,
                max_hourly_demand_change_mw=max(map(abs,deltas)))


def check_built_fixed_ev(ctx, model, year, series, limits):
    """Verify the native fixed schedule, including initial and terminal energy."""
    if (year not in ctx['years'] or tuple(ctx['periods']) != tuple(range(1,HOURS+1))
            or any(len(series[k]) != HOURS for k in ('ev_charge','ev_driving','ev_available'))):
        raise ValueError('Built EV audit expects the native hourly horizon')
    eta = limits['ev_efficiency']
    initial = limits['ev_initial_mwh']
    capacity = limits['ev_usable_storage_mwh']
    model.update()

    def near(a,b):
        return isfinite(a) and abs(a-b) <= HOURLY_TOL

    def matches_row(row, expected, rhs, sense='='):
        expression = model.getRow(row)
        values = {expression.getVar(i).index:expression.getCoeff(i) for i in range(expression.size())}
        return row.Sense == sense and near(row.RHS,rhs) and values.keys() == expected.keys() and all(
            near(values[key],value) for key,value in expected.items())

    prior = None
    for i,(grid,drive,available) in enumerate(zip(series['ev_charge'],series['ev_driving'],series['ev_available']),1):
        key = (i,year)
        charge,out,soc = (ctx[name][key] for name in ('ev_sto_in','ev_sto_out','ev_sto_soc'))
        equation = ctx['ev_storage_soc_0'][year] if prior is None else ctx['ev_storage_soc'][key]
        terms = {soc.index:1.,out.index:1.,charge.index:-eta}
        if prior is not None:
            terms[prior.index] = -1.
        rhs = (initial if prior is None else 0.)-drive
        if (not all(near(x,grid) for x in (charge.LB,charge.UB))
                or not near(out.LB,0.) or not near(out.UB,0.)
                or not matches_row(equation,terms,rhs)
                or not matches_row(ctx['ev_storage_max'][key],{soc.index:1.},capacity,'<')
                or not matches_row(ctx['ev_storage_min'][key],{soc.index:1.},0.,'>')
                or not matches_row(ctx['ev_powerIN_max'][key],{charge.index:1.},limits['ev_connection_mw']*available,'<')):
            raise ValueError(f'Built EV schedule or storage differs in hour {i}')
        if i == HOURS and (not near(soc.LB,initial) or not near(soc.UB,initial)):
            raise ValueError('Built EV terminal SOC is not fixed to its initial usable value')
        prior = soc
    return dict(status='built_fixed_ev_passed',hours=HOURS,year=year)

"""Validate and wire the common verified inputs for a matrix-only zonal build."""

from pathlib import Path

from zonal_2030_boundaries import configure_neighbor_inventory, install_neighbor_rows, read_neighbor_envelope
from zonal_2030_physical_inputs import (
    check_demand_meter, configure_storage_stock, dated_batteries, install_demand_meter,
    install_grid_battery_limits, read_json, read_verified_meter,
)
from zonal_2030_weather import digest as digest_file


CASES = ('avg','low','high',*(f'historical_{y}' for y in range(2020,2025)))


def validate_weather(root):
    report = read_json(root/'weather_preflight.json')
    if (report['status'] != 'input_preflight_only' or report['no_optimization'] is not True
            or set(report['cases']) != set(CASES)):
        raise ValueError('Expected all eight verified full-hour weather cases')
    for key in CASES:
        case = report['cases'][key]
        path = root/(key+'_ncre.csv')
        if digest_file(path) != case['mapped_ncre_sha256']:
            raise ValueError('Remapped weather bytes changed: '+key)
        if set(case['spatial']) != {'solar','wind'} or any(
                not 0. <= data['max_hourly_cf_error'] <= 1e-8 for data in case['spatial'].values()):
            raise ValueError('Hourly national CF did not close: '+key)
    return report


def ready_inputs(root):
    """Return verified sources without opening the model or allocating a solver session."""
    demand,demand_meta = read_verified_meter(root)
    boundary = root/'zonal_2030_boundary_preflight'
    neighbor = read_neighbor_envelope((boundary/'neighbor_hourly.csv').read_bytes(),
                                      read_json(boundary/'neighbor_preflight.json'))
    dated = read_json(root/'zonal_dated_stock_sources.json')
    battery_policy = read_json(root/'future_stationary_storage_policy.json')
    battery = dated_batteries(dated,battery_policy)[2030]
    return demand,demand_meta,neighbor,battery


def make_hooks(root, install_qualified_stock, install_qualified_hydro, audit_qualified_stock, audit_qualified_hydro):
    """Compose the existing native hooks. An inventory and hydro audit are mandatory."""
    if not all(callable(f) for f in (install_qualified_stock,install_qualified_hydro,
                                     audit_qualified_stock,audit_qualified_hydro)):
        raise ValueError('No matrix admission without qualified generation and water hooks')
    demand,demand_meta,neighbor,battery = ready_inputs(root)
    links = None

    def before_variables(ctx):
        install_qualified_stock(ctx)
        install_demand_meter(ctx,demand)

    def before_matrix(ctx):
        nonlocal links
        if links is not None:
            raise ValueError('A zonal hook instance is single-use')
        configure_storage_stock(ctx,battery)
        links = configure_neighbor_inventory(ctx,neighbor,ctx['_eraa_2030_fixed'][0])
        install_qualified_hydro(ctx)

    def after_matrix(ctx):
        if links is None:
            raise ValueError('Native zonal inventory has not been installed')
        ctx['_zonal_neighbor_rows']=install_neighbor_rows(ctx,neighbor,links)
        ctx['physical_zonal_audit'] = dict(
            demand_annual_mwh=check_demand_meter(ctx,{(s,p): v for s,values in demand.items()
                                                   for p,v in enumerate(values,1)},demand_meta['annual_mwh']),
            battery=install_grid_battery_limits(ctx,battery),
            generation=audit_qualified_stock(ctx),hydro=audit_qualified_hydro(ctx),
            si_ba_directional_rows=len(links)*8760*2,
            no_optimization=True,
        )

    return dict(before_variables=before_variables,before_matrix=before_matrix,after_matrix=after_matrix)


def qualification_receipt(root,weather_root=None):
    demand,demand_meta,neighbor,battery = ready_inputs(root)
    hydro = root/'zonal_2030_hydro_preflight'
    report = read_json(hydro/'preflight.json')
    if digest_file(hydro/'absolute_natural_hydro.csv') != report['hourly_sha256']:
        raise ValueError('Absolute hydro source changed')
    weather = validate_weather(weather_root) if weather_root else None
    return dict(status='physical_sources_prepared_native_matrix_pending',no_optimization=True,
                production_approved=False,prepared_demand_rows=sum(map(len,demand.values())),
                demand_annual_mwh=demand_meta['annual_mwh'],prepared_neighbor_directional_rows=len(neighbor)*4,
                existing_battery_grid_mw_mwh=battery,weather_cases=sorted(weather['cases']) if weather else None,
                hydro_source_rows=report['rows'],open_water_checks=report['open_checks'])


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weather-root',type=Path)
    args = parser.parse_args()
    print(json.dumps(qualification_receipt(Path(__file__).resolve().parent,args.weather_root),indent=2))

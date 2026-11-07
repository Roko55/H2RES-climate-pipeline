"""Create the per-case demand receipt required by the production worker."""

import argparse
from hashlib import sha256
import json
from math import fsum
from pathlib import Path

from multisector_fixed_demand import (
    HEAT_OFFSETS, HEAT_SPLIT, HOURLY, MANIFEST, apply_heat_meter_split, load_schedules,
)


HEAT = Path('data/SC/heat_demand_HR_2020_2050_sdewes.csv')
RECEIPT = 'future_demand_physical_audit.json'


def write_demand_receipt(project, case, frozen):
    """Validate the fixed schedules and link the separate forecast and meter inputs."""
    project = project.resolve()

    def evidence(path):
        relative = path.as_posix()
        digest = sha256((project/path).read_bytes()).hexdigest()
        if digest != frozen.get(relative):
            raise ValueError('Unpinned demand input: '+relative)
        return dict(path=relative,sha256=digest)

    schedule = load_schedules(project,frozen,case)
    adjusted, split = apply_heat_meter_split(project,frozen,schedule)
    if evidence(HEAT)['sha256'] != split['outputs_sha256'][HEAT.name]:
        raise ValueError('Heat forecast differs from the gross meter receipt')
    windows = []
    for year,(series,_) in adjusted.items():
        original = schedule[year][0]['residual_power']
        excluded_heat = fsum(series['heat_reference'])
        excluded_pump = fsum(series['pumping_reference'])
        closure = fsum((fsum(original),-fsum(series['residual_power']),-excluded_heat,-excluded_pump))
        if abs(closure) > .01:
            raise ValueError('Future gross meter failed annual reconciliation in '+str(year))
        windows.append(dict(year=year,status='input_physical_checks_passed',hours=len(original),
                            ev_grid_mwh=fsum(series['ev_charge']),cooling_grid_mwh=fsum(series['cooling_electric']),
                            excluded_heat_mwh=excluded_heat,excluded_pumping_mwh=excluded_pump,
                            meter_closure_mwh=closure))
    report = dict(status='approved_for_planning',case=case,
                  exogenous_power_excludes_native_hydro_pumping=True,
                  independent_limits=evidence(MANIFEST),runtime_input_manifest=evidence(HOURLY),
                  heat_meter=evidence(HEAT_SPLIT),heat_offsets=evidence(HEAT_OFFSETS),
                  heat_forecast=evidence(HEAT),windows=windows)
    with (project/RECEIPT).open('x',encoding='utf8') as stream:
        json.dump(report,stream,indent=2,allow_nan=False)
        stream.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project',type=Path)
    parser.add_argument('case')
    parser.add_argument('case_frozen_files',type=Path)
    args = parser.parse_args()
    write_demand_receipt(args.project,args.case,json.loads(args.case_frozen_files.read_bytes()))


if __name__ == '__main__':
    main()

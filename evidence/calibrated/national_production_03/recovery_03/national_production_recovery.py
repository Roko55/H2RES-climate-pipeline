"""Resume the pinned stage after correcting the rolling-window input observer."""

import argparse
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

from national_window_continuation import absolute_hydro_window, district_window, model_local_names, signature_observer


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENTRY = ROOT/'full_worker.py'


def require_owned_root():
    if (socket.gethostname().lower() != 'pcsp-mxl0493vpr'
            or ROOT != Path('E:/Roko/remote_access/national_production_03')
            or HERE.name != 'recovery_03'):
        raise ValueError('Use the owned stage 03 recovery directory')
    sys.path[:0] = [str(ROOT), str(ROOT.parent/'energy_rebuild_20261019/runtime_packages')]


def verify_sources():
    import pandas as pd
    from common import digest, load, sha
    from finalize_national_preflight import require_national_admission
    from multisector_planning_integration import CASES
    from seed_planning_investments import HORIZON

    preparation = require_national_admission(ROOT)
    frozen = preparation['frozen_files']
    def pinned(relative):
        path = ROOT/relative
        if sha(path) != frozen[relative]:
            raise ValueError('Pinned recovery dependency changed: '+relative)
        return path
    for relative in ('full_worker.py','hooks.py','national_production_controller.py',
                     'multisector_district_heat.py','multisector_hydro_integration.py','future_absolute_hydro.py'):
        pinned(relative)
    native = pinned('sources/Build_model_RH.py')
    containers = model_local_names(native.read_text(encoding='utf8'))
    fuel_hashes = set()
    for case in CASES:
        inventory = pinned(f'projects/{case}/data/SC/genco_data_HR_sdewes.csv')
        native_model = pinned(f'projects/{case}/scripts/Build_model_RH.py')
        if sha(native_model) != sha(native):
            raise ValueError('Native case model differs from the reviewed source')
        fuel = list(pd.read_csv(inventory,sep=r',|;|@|\s+',engine='python')['fuel_type'].unique())
        fuel_hashes.add(digest(fuel))
    for year in HORIZON[1:]:
        inputs = load(pinned(f'reference/{year}/prebuild_validation.json'))['common_input_hashes']
        if (set(inputs) & (containers | {'Fossil_hydro'})
                or not {'fuel','Thermal_Decom_DH'} <= inputs.keys() or fuel_hashes != {inputs['fuel']}):
            raise ValueError('The recovered observer does not reproduce the frozen references')
    return preparation, containers


def install(case, containers):
    import hooks
    import multisector_district_heat as heat
    import multisector_hydro_integration as hydro
    from common import load, now, save

    def reference(year):
        return load(ROOT/'reference'/str(year)/'prebuild_validation.json')
    def record(year, data):
        path = HERE/'input_checks'/case/f'{year}.json'
        save(path,dict(utc=now(),case=case,year=year,**data))
    hooks.input_signatures = signature_observer(hooks.input_signatures,containers,reference,record)
    heat.prepare_district_heat = district_window(heat.prepare_district_heat)
    hydro.install_absolute_run_of_river = absolute_hydro_window(hydro.install_absolute_run_of_river)


def run_worker(args):
    if len(args) != 5 or Path(args[0]).resolve() != ENTRY or args[3] != args[2] or args[4] != '0':
        raise ValueError('Unexpected native recovery worker arguments')
    _, containers = verify_sources()
    install(args[2],containers)
    sys.argv = args
    runpy.run_path(str(ENTRY),run_name='__main__')


def run_controller():
    import national_production_controller as controller
    from common import identity, lease, load, now, save, sha

    report = HERE/'status.json'
    _, _ = verify_sources()
    origin = ROOT/'coordination/status.json'
    target = ROOT/'status.json'
    controller_entry = ROOT/'national_production_controller.py'
    original_popen = subprocess.Popen

    def launch(command, **kwargs):
        if len(command) != 8 or Path(command[3]).resolve() != ENTRY:
            raise ValueError('Unexpected launch by pinned production controller')
        return original_popen([*command[:3],str(Path(__file__).resolve()),'worker',*command[3:]],**kwargs)

    # Keep the pinned controller, including its shared resource gate, lease and checkpoints.
    controller.subprocess = SimpleNamespace(**{**vars(subprocess),'Popen':launch})
    with lease('national_production_recovery_03'):
        try:
            previous = ROOT/'recovery_02'
            previous_terminal = previous/'status.json'
            save(report,dict(utc=now(),status='waiting_for_previous_recovery',pid=os.getpid()))
            while True:
                parent = load(origin)
                status = load(target)
                last = load(previous_terminal)
                if (parent['status'] in ('stopped','planning_completed')
                        and last['status'] in ('stopped','planning_completed','original_completed')):
                    break
                time.sleep(30)
            if last['status'] in ('planning_completed','original_completed'):
                if status['status']!='completed_verified' or last['planning_status_sha256']!=sha(target):
                    raise ValueError('Previous controller and coordinator results differ')
                save(report,dict(utc=now(),status='previous_completed',planning_status_sha256=sha(target)))
                return
            if (status['status']!='incomplete' or status.get('worker') is not None or not status['failed']
                    or any(identity(case['worker']) is not None for case in status['failed'].values())):
                raise ValueError('The previous recovery did not release the native worker')
            if not any("bad operand type for abs(): 'gurobipy._core.LinExpr'" in
                       (ROOT/'records'/case/'worker.log').read_text(encoding='utf8',errors='replace')
                       for case in status['failed']):
                raise ValueError('No previous rolling hydro audit failure found')
            before = HERE/'original_production_status.json'
            with before.open('xb') as stream:
                stream.write((previous/'original_production_status.json').read_bytes())
            prefix = HERE/'previous_production_status.json'
            with prefix.open('xb') as stream:
                stream.write(target.read_bytes())
            evidence = dict(original_status_sha256=sha(before),original_coordination_sha256=sha(origin),
                            previous_recovery_status_sha256=sha(previous_terminal),
                            previous_production_status_sha256=sha(prefix),
                            original_controller_sha256=sha(controller_entry),
                            recovery_code_sha256={n:sha(HERE/n) for n in
                                                  ('national_production_recovery.py','national_window_continuation.py')})
            save(report,dict(utc=now(),status='resuming_verified_prefixes',pid=os.getpid(),**evidence))
            controller.run()
            if load(target)['status']!='completed_verified':
                raise ValueError('Recovery stopped before all 42 windows were accepted')
            save(report,dict(utc=now(),status='planning_completed',planning_status_sha256=sha(target),**evidence))
        except BaseException:
            save(report,dict(utc=now(),status='stopped',error=traceback.format_exc()))
            raise


if __name__ == '__main__':
    require_owned_root()
    parser = argparse.ArgumentParser()
    parser.add_argument('mode',choices=('verify','controller','worker'))
    parser.add_argument('worker_args',nargs='*')
    args = parser.parse_args()
    if args.mode=='verify':
        verify_sources()
    elif args.mode=='controller':
        run_controller()
    else:
        run_worker(args.worker_args)

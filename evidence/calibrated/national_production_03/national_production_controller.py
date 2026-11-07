"""Run the seven admitted national paths as detached resumable server jobs."""

from pathlib import Path
import os
import subprocess
import sys
import time

import psutil

from common import R, identity, lease, load, now, remote_only, save, sha
import electricity_matrix_2030 as resources
from finalize_national_preflight import require_national_admission
from multisector_planning_integration import CASES
from seed_planning_investments import HORIZON


WORKER = R/'full_worker.py'


def accepted_years(case, preparation):
    found = []
    job = R/'jobs'/case
    for year in HORIZON[1:]:
        window = job/str(year)
        path = window/'accepted.json'
        if not path.exists():
            break
        marker = load(path)
        solver = load(window/'solver_result.json')
        if (marker['case'] != case or marker['year'] != year
                or marker['fence'] != preparation['execution_fence']
                or marker['historical_base_sha256'] != preparation['historical_base_sha256']
                or solver['status'] != 2 or max(solver['constraint_violation'],solver['bound_violation']) > 1e-7
                or any(sha(window/name) != marker['files'][name] for name in (
                    'solver_result.json','after_window.pkl','capital_handoff_audit.json',
                    'production_supply_audit.json','power_policy_solution_audit.json'))):
            raise ValueError(f'Accepted native window failed readback: {case}/{year}')
        found.append(year)
    if any((job/str(year)/'accepted.json').exists() for year in HORIZON[len(found)+1:]):
        raise ValueError('Native capital handoff contains a gap: '+case)
    return found


def adopt(case):
    root = WORKER.as_posix().lower()
    for process in psutil.process_iter(['cmdline','create_time']):
        cmd = process.info['cmdline'] or []
        for i,arg in enumerate(cmd):
            if arg.replace('\\','/').lower() == root and cmd[i+2:i+5] == [case,case,'0']:
                return dict(pid=process.pid,created=process.info['create_time'],script=str(WORKER))
    return None


def verify_case_sources(case, preparation):
    for relative,digest in preparation['frozen_files'].items():
        if relative.startswith('projects/') and not relative.startswith(f'projects/{case}/'):
            continue
        if sha(R/relative) != digest:
            raise ValueError('Frozen production source changed: '+relative)


def run():
    remote_only()
    resources.ROOT = R
    with lease('national_production_controller'):
        preparation = require_national_admission(R)
        progress = dict(utc=now(),status='admitted',pid=os.getpid(),execution_fence=preparation['execution_fence'],
                        completed={},failed={})
        records = R/'records'
        records.mkdir(exist_ok=True)
        for case in CASES:
            verify_case_sources(case,preparation)
            before = accepted_years(case,preparation)
            progress['completed'][case] = before
            if before == list(HORIZON[1:]):
                continue
            worker = None
            record = adopt(case)
            if record is None:
                while True:
                    admission = resources.resource_check()
                    progress.update(utc=now(),status='waiting_for_resources',current_case=case,resources=admission)
                    save(R/'status.json',progress)
                    if admission['ready']:
                        break
                    time.sleep(30)
                run_dir = records/case
                run_dir.mkdir(exist_ok=True)
                env = dict(os.environ,CHANNEL_CASE=case,PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1',
                           OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',MPLBACKEND='Agg',
                           TMP=str(run_dir),TEMP=str(run_dir),MPLCONFIGDIR=str(run_dir/'mpl'))
                with (run_dir/'worker.log').open('ab') as log:
                    worker = subprocess.Popen([sys.executable,'-u','-B',str(WORKER),str(R/'projects'/case),
                                               case,case,'0'],cwd=R,env=env,stdin=subprocess.DEVNULL,
                                              stdout=log,stderr=subprocess.STDOUT,
                                              creationflags=subprocess.CREATE_NO_WINDOW)
                record = dict(pid=worker.pid,created=psutil.Process(worker.pid).create_time(),script=str(WORKER))
            save(records/f'{case}.json',record)
            progress.update(utc=now(),status='running',current_case=case,worker=record)
            save(R/'status.json',progress)
            while (worker.poll() is None if worker is not None else identity(record) is not None):
                time.sleep(30)
                live = R/'jobs'/case/'progress.json'
                try:
                    heartbeat = load(live) if live.exists() else None
                except OSError:
                    heartbeat = None
                progress.update(utc=now(),last_worker_progress=heartbeat)
                save(R/'status.json',progress)
            after = accepted_years(case,preparation)
            progress['completed'][case] = after
            if after != list(HORIZON[1:]):
                progress['failed'][case] = dict(accepted_years=after,worker=record)
        progress.update(utc=now(),status='incomplete' if progress['failed'] else 'completed_verified',
                        current_case=None,worker=None)
        save(R/'status.json',progress)


if __name__ == '__main__':
    run()

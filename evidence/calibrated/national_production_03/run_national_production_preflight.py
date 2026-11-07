"""Freeze six no-solve receipts, then build one national matrix without optimizing."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback

import psutil

from electricity_project_integration import digest_file
import electricity_matrix_2030 as admission
from finalize_national_preflight import approve_dry_build, pin_references


ROOT = Path(__file__).resolve().parent
YEARS = tuple(range(2025,2051,5))
OUTPUT = ROOT/'production_preflight'


def load(path):
    return json.loads(path.read_bytes())


def save(path,data):
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf8')
    pending.replace(path)


def utc():
    return datetime.now(timezone.utc).isoformat()


def checked_worker(name,anchor):
    path = ROOT/name
    staged = load(ROOT/'preparation.json')
    if digest_file(path) != staged['frozen_files'][name]:
        raise ValueError('Frozen preflight worker changed')
    args = [sys.executable,'-B','-u',str(path),str(ROOT/'projects/average'),'average','average',str(anchor)]
    with (OUTPUT/(path.stem+'_'+str(anchor)+'.log')).open('xb') as log:
        env = dict(os.environ,CHANNEL_CASE='average',PYTHONDONTWRITEBYTECODE='1',
                   OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
                   MPLBACKEND='Agg',TMP=str(OUTPUT),TEMP=str(OUTPUT),MPLCONFIGDIR=str(OUTPUT/'mpl'))
        proc = subprocess.Popen(args,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        child = dict(pid=proc.pid,created=psutil.Process(proc.pid).create_time(),script=str(path))
        save(OUTPUT/'worker.json',child)
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f'{name} {anchor} exited with {rc}; inspect its log')


def run():
    if (socket.gethostname().lower()!='pcsp-mxl0493vpr'
            or ROOT.parent != Path('E:/Roko/remote_access') or not ROOT.name.startswith('national_production_')):
        raise RuntimeError('Preflight requires the scoped H2RES server')
    OUTPUT.mkdir(exist_ok=False)
    status = {'utc':utc(),'status':'starting','completed_reference_anchors':[]}
    try:
        prep = load(ROOT/'preparation.json')
        if prep['status']!='staged_for_common_input_capture':
            raise ValueError('Expected a fresh national stage')
        with (OUTPUT/'original_preparation.json').open('xb') as f:
            f.write((ROOT/'preparation.json').read_bytes())
        for year in YEARS:
            status.update(utc=utc(),status='capturing_reference',year=year)
            save(OUTPUT/'status.json',status)
            checked_worker('reference_worker.py',year)
            status['completed_reference_anchors'].append(year)
        frozen = pin_references(ROOT)
        admission.ROOT=ROOT
        while True:
            resources=admission.resource_check()
            status.update(utc=utc(),status='waiting_for_matrix_slot',resources=resources)
            save(OUTPUT/'status.json',status)
            if resources['ready']:
                break
            time.sleep(60)
        status.update(utc=utc(),status='building_matrix')
        save(OUTPUT/'status.json',status)
        checked_worker('dry_build_worker.py',0)
        report_path=ROOT/'dry_build_jobs/average/dry_build.json'
        report=load(report_path)
        if (report['status']!='matrix_built_without_optimization' or report['solver_invocations']!=0
                or report['planning_approved'] is not False or report['years']!=[2025,2030]
                or report['fence']!=frozen['execution_fence'] or any((ROOT/'dry_build_jobs').rglob('accepted.json'))):
            raise ValueError('Invalid terminal national matrix proof')
        approve_dry_build(ROOT)
        status.update(utc=utc(),status='reference_and_matrix_preflight_completed',
                      matrix_report_sha256=digest_file(report_path),fingerprint=report['fingerprint'],
                      no_optimization=True,production_launched=False)
        save(OUTPUT/'status.json',status)
    except BaseException:
        status.update(utc=utc(),status='stopped',error=traceback.format_exc())
        save(OUTPUT/'status.json',status)
        raise


if __name__ == '__main__':
    run()

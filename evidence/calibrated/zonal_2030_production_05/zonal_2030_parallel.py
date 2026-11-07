"""Schedule the existing, individually audited zonal jobs in up to two WLS slots."""

from collections import deque
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import psutil
import zonal_2030_production as production


ROOT = production.ROOT
SCRIPT = Path(__file__).resolve()
STATUS = ROOT / "parallel_status.json"
MAX_WORKERS = 2
TOKEN_PAUSE = 300


def has_command(process, script, mode):
    args = process.cmdline()
    return len(args) >= 2 and Path(args[0]).name.lower() == "python.exe" and any(
        Path(arg) == script and args[i + 1] == mode for i, arg in enumerate(args[:-1])
    )


def eligible_jobs(pending, completed):
    """A dispatch job can start as soon as its own portfolio is accepted."""
    return [item for item in pending
            if item[1] is None or (item[0], None, 1.0) in completed]


def resources(controller):
    """Apply the original limits while allowing workers of this controller only."""
    external, inaccessible = [], []
    for proc in psutil.process_iter(["name"]):
        if not (proc.info["name"] or "").lower().startswith(("python", "gurobi", "grb")):
            continue
        if proc.pid == os.getpid():
            continue
        try:
            if proc.ppid() == controller and has_command(proc, SCRIPT, "worker"):
                continue
            if ((proc.info["name"] or "").lower().startswith(("gurobi", "grb"))
                    or any("gurobi" in Path(m.path).name.lower() for m in proc.memory_maps())):
                external.append(proc.pid)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            inaccessible.append(proc.pid)
    ram = psutil.virtual_memory().available / 2**30
    disk = shutil.disk_usage(ROOT).free / 2**30
    return dict(available_ram_gib=round(ram, 2), available_disk_gib=round(disk, 2),
                other_gurobi_process_count=len(external), inaccessible_python_count=len(inaccessible),
                ready=ram >= 160 and disk >= 105 and not external and not inaccessible)


def verified_controller(pid, created):
    try:
        proc = psutil.Process(pid)
        if abs(proc.create_time() - created) > 0.01 or not has_command(
            proc, production.SCRIPT, "controller"
        ):
            raise ValueError("Original controller identity changed")
        return proc
    except psutil.NoSuchProcess:
        return None


def take_over(pid, created):
    """Stop the identified controller only when it has no active child."""
    while True:
        proc = verified_controller(pid, created)
        if proc is None:
            if production.load(ROOT / "status.json")["stage"] in ("completed_verified", "stopped"):
                raise ValueError("The original controller completed or stopped before handoff")
            return
        proc.suspend()
        try:
            state = production.load(ROOT / "status.json")
            if state["pid"] != pid or state["stage"] in ("completed_verified", "stopped"):
                raise ValueError("The original controller completed or stopped before handoff")
            if not proc.children(recursive=True):
                proc.terminate()
                proc.wait(timeout=15)
                return
        finally:
            try:
                proc.resume()
            except psutil.NoSuchProcess:
                pass
        time.sleep(0.2)


def run_worker(label, year, fraction, target):
    parent = psutil.Process().parent()
    if parent is None or not has_command(parent, SCRIPT, "controller"):
        raise ValueError("Worker was not started by the owned parallel controller")
    production.matrix.resource_check = lambda: resources(parent.pid)
    try:
        production.worker(label, year, fraction, target)
    except BaseException as error:
        message = str(error).lower()
        license_limit = ("too many sessions" in message
                         or ("wls" in message and "session limit" in message))
        admission_lost = message == "resource admission was lost before model creation"
        production.save(target / "failure.json", dict(error=traceback.format_exc(),
                        license_limit=license_limit, admission_lost=admission_lost,
                        utc=production.batch.utc()))
        raise


def start_worker(item):
    name = production.pin_key(*item)
    job = ROOT / "jobs" / name
    for failure in job.glob("attempt_*/failure.json"):
        report = production.load(failure)
        if not (report.get("license_limit") or report.get("admission_lost")):
            raise ValueError("Previous attempt stopped on evidence: " + name)
    number = max((int(p.name.split("_")[-1]) for p in job.glob("attempt_*")), default=0) + 1
    attempt = job / f"attempt_{number:02d}"
    attempt.mkdir(parents=True, exist_ok=False)
    label, year, fraction = item
    env = dict(os.environ, TMP=str(attempt), TEMP=str(attempt),
               MPLCONFIGDIR=str(attempt / "mpl"), OMP_NUM_THREADS="1",
               MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    with (attempt / "worker.log").open("xb") as log:
        worker = subprocess.Popen([sys.executable, "-B", "-u", str(SCRIPT), "worker",
                                   label, str(year or 0), str(fraction), str(attempt)],
                                  cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
    return worker, attempt


def run():
    controller = os.getpid()
    state = dict(pid=controller, completed=[], total=len(production.TASKS), workers=[])

    def publish(stage, **extra):
        state.update(utc=production.batch.utc(), stage=stage, **extra)
        production.save(ROOT / "status.json", state)
        production.save(STATUS, state)

    with production.controller_lock():
        try:
            while True:
                gate = production.gate()
                if gate and production.national_recovery_finished():
                    break
                publish("waiting_for_updated_matrix")
                time.sleep(5)
            fence, _ = gate
            completed = {}
            pending = deque()
            for item in production.TASKS:
                result = production.receipt(ROOT, *item, fence)
                if result:
                    completed[item] = result
                else:
                    pending.append(item)
            active, failures = {}, {}
            limit, paused_until = MAX_WORKERS, 0
            stopped_on = None
            while pending or active:
                for item, (worker, attempt) in list(active.items()):
                    if worker.poll() is None:
                        continue
                    del active[item]
                    result = production.receipt(ROOT, *item, fence)
                    if result:
                        completed[item] = result
                        continue
                    failure_path = attempt / "failure.json"
                    failure = production.load(failure_path) if failure_path.is_file() else {}
                    retries = failures.get(item, 0)
                    if retries == 0 and (failure.get("license_limit") or failure.get("admission_lost")):
                        failures[item] = retries + 1
                        pending.appendleft(item)
                        if failure.get("license_limit"):
                            limit, paused_until = 1, time.monotonic() + TOKEN_PAUSE
                    else:
                        stopped_on = production.pin_key(*item)

                capacity = limit - len(active)
                ready = [] if stopped_on else eligible_jobs(pending, completed)
                if stopped_on and not active:
                    raise ValueError("Worker did not pass: " + stopped_on)
                if pending and not active and not ready:
                    raise ValueError("Pending dispatch lacks an accepted portfolio")
                available = resources(controller) if capacity > 0 and ready else None
                if available and available["ready"] and time.monotonic() >= paused_until:
                    for item in ready[:capacity]:
                        pending.remove(item)
                        active[item] = start_worker(item)

                state["completed"] = [
                    dict(job=production.pin_key(*item), status=result[0]["status"],
                         marker_sha256=production.digest(result[1]))
                    for item in production.TASKS if (result := completed.get(item))
                ]
                workers = [dict(job=production.pin_key(*item), pid=process.pid)
                           for item, (process, _) in active.items()]
                publish("running" if workers else "waiting_for_resources", workers=workers,
                        current=workers[0]["job"] if workers else None, max_workers=limit,
                        resources=available, pending=len(pending))
                if pending or active:
                    time.sleep(3 if active else 10)
            production.save(ROOT / "results.json", dict(diagnostic_sha256=fence, cases=state["completed"]))
            publish("completed_verified", workers=[], current=None,
                    results_sha256=production.digest(ROOT / "results.json"))
        except BaseException:
            publish("stopped", error=traceback.format_exc())
            raise


def launch():
    state = production.load(ROOT / "status.json")
    if state["stage"] in ("completed_verified", "stopped") or "workers" in state:
        raise ValueError("Expected the active original sequential controller")
    proc = psutil.Process(state["pid"])
    created = proc.create_time()
    verified_controller(proc.pid, created)
    production.gate()
    with (ROOT / "parallel_controller.log").open("xb") as log:
        detached = subprocess.Popen([sys.executable, "-B", "-u", str(SCRIPT), "controller",
                                     str(proc.pid), str(created)],
                                    cwd=ROOT, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
    production.save(ROOT / "parallel_launch.json", dict(pid=detached.pid,
                    created=psutil.Process(detached.pid).create_time(), original_pid=proc.pid))


if __name__ == "__main__":
    production.require_owned()
    mode, *args = sys.argv[1:]
    if mode == "launch" and not args:
        launch()
    elif mode == "controller" and len(args) == 2:
        try:
            production.save(STATUS, dict(stage="waiting_for_safe_handoff", pid=os.getpid()))
            take_over(int(args[0]), float(args[1]))
            run()
        except BaseException:
            state = production.load(STATUS)
            if state.get("stage") != "stopped":
                production.save(STATUS, dict(stage="handoff_stopped", error=traceback.format_exc()))
            raise
    elif mode == "worker" and len(args) == 4:
        label, year, fraction, target = args
        run_worker(label, int(year) or None, float(fraction), Path(target).resolve())
    else:
        raise ValueError("Unexpected zonal parallel controller command")

"""Solve an unchanged native matrix with independently checked precision settings."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import time

HERE = Path('E:\\Roko\\remote_access\\minimal_correction_20261021')
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def accepted(model, fingerprint):
    return (model.Status == 2 and model.SolCount > 0 and int(model.Fingerprint) == fingerprint
            and model.ConstrVio <= 1e-7 and model.BoundVio <= 1e-7)


def policies():
    selected = []
    for case in ('avg', 'high'):
        result = json.loads((HERE / 'numeric_probe_01' / case / 'status.json').read_text())
        if result['status'] != 'passed' or not result.get('matrix_matches_failed_window'):
            raise RuntimeError('The isolated same-matrix precision probe has not passed: ' + case)
        params = result['accepted_parameters']
        if params not in selected:
            selected.append(params)
    for params in ({'Method': 2, 'ScaleFlag': 1, 'Aggregate': 0, 'Presolve': 2},
                   {'Method': 2, 'ScaleFlag': 0, 'Aggregate': 0, 'Presolve': 2},
                   {'Method': 1, 'ScaleFlag': 0, 'Aggregate': 0, 'Presolve': 0}):
        if params not in selected:
            selected.append(params)
    return selected


def solve(model, original_optimize, record_path, args=(), kwargs=None):
    model.update()
    fingerprint = int(model.Fingerprint)
    record_path = Path(record_path).resolve()
    record_path.relative_to(Path('E:\\Roko\\remote_access\\resource_batch_20261029').resolve())
    record_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'fingerprint': fingerprint, 'attempts': [],
              'started_utc': datetime.now(timezone.utc).isoformat(), 'equations_modified': False,
              'precision_implementation_sha256': IMPLEMENTATION_SHA256,
              'primal_acceptance': 1e-7, 'bound_acceptance': 1e-7}
    started = time.monotonic()
    for parameters in policies():
        model.reset()
        common = {'Threads': 4, 'NumericFocus': 3, 'Quad': 1, 'MarkowitzTol': 0.5,
                  'Crossover': 1, 'FeasibilityTol': 1e-9, 'OptimalityTol': 1e-9,
                  'BarConvTol': 1e-10, 'TimeLimit': 10800}
        for name, value in {**common, **parameters}.items():
            model.setParam(name, value)
        original_optimize(model, *args, **(kwargs or {}))
        item = {'parameters': {**common, **parameters}, 'Status': model.Status,
                'SolCount': model.SolCount, 'Runtime': model.Runtime,
                'fingerprint': int(model.Fingerprint),
                'matrix_preserved': int(model.Fingerprint) == fingerprint}
        if model.SolCount:
            item.update(ObjVal=model.ObjVal, ObjBound=model.ObjBound,
                        ConstrVio=model.ConstrVio, BoundVio=model.BoundVio, DualVio=model.DualVio)
        report['attempts'].append(item)
        passed = accepted(model, fingerprint)
        report.update(status='passed' if passed else 'running', wall_seconds=time.monotonic() - started)
        record_path.write_text(json.dumps(report, indent=2))
        if passed:
            return report
        if int(model.Fingerprint) != fingerprint:
            raise RuntimeError('The native matrix changed during numerical solution')
    report['status'] = 'failed'
    record_path.write_text(json.dumps(report, indent=2))
    try:
        model.write(str(record_path.with_suffix('.mps')))
    except Exception as error:
        report['optional_model_export_error'] = str(error)
        record_path.write_text(json.dumps(report, indent=2))
    raise RuntimeError('No same-matrix precision policy met the unchanged acceptance gate')

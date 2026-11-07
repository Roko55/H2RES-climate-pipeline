"""Execute unchanged archived model equations with a solver-quality recorder."""
from pathlib import Path
from datetime import datetime, timezone
import inspect
import json
import os
import pickle
import runpy
import sys

BASE = Path('E:/Roko/remote_access/energy_rebuild_20261019')
sys.path.insert(0, str(BASE / 'runtime_packages'))
import gurobipy as gp

PROJECT = Path(sys.argv[1]).resolve()
OUT = Path(os.environ['LEGACY_AUDIT_DIR']).resolve()
for target in (PROJECT,OUT):
    relative=target.relative_to(Path('E:\\Roko\\remote_access\\resource_batch_20261029'))
    if relative.parts[0] not in ('legacy_energy_01','legacy_whole_path_03'):
        raise ValueError('Unexpected owned legacy cohort')
OUT.mkdir(parents=True, exist_ok=True)
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))
OriginalModel = gp.Model


class AuditedModel(OriginalModel):
    def optimize(self, *args, **kwargs):
        self.update()
        context = inspect.currentframe().f_back.f_globals
        window_years = [int(v) for v in context.get('years', [])]
        def constant(value):
            if isinstance(value, (int, float)):
                return float(value)
            if hasattr(value, 'getValue') and value.size() == 0:
                return float(value.getValue())
            raise TypeError('Native inherited-state value is not a scalar constant')
        inherited = {name: {key: constant(value) for key, value in values.items()}
                     for name, values in context.items()
                     if isinstance(values, dict) and
                     (name.endswith('_cumulative') or name.startswith('values_'))}
        if window_years:
            snapshot = {'years': window_years, 'hor_yrs': list(context['hor_yrs']),
                        'index': int(context['i']), 'state': inherited}
            with (OUT / ('inherited_' + str(min(window_years)) + '.pkl')).open('wb') as stream:
                pickle.dump(snapshot, stream, protocol=4)
        self.Params.Threads = 4
        self.Params.NumericFocus = 3
        if os.environ.get('LEGACY_PRECISE_START') == '1':
            sys.path.insert(0, 'E:\\Roko\\remote_access\\resource_batch_20261029\\helpers')
            from precision_policy import solve
            solve(self, OriginalModel.optimize,
                  OUT / ('precise_' + '_'.join(map(str, window_years)) + '.json'), args, kwargs)
        else:
            OriginalModel.optimize(self, *args, **kwargs)
        recovery = None
        if os.environ.get('LEGACY_NUMERIC_RECOVERY')=='1' and self.Status==2 and (
                self.ConstrVio>1e-5 or self.BoundVio>1e-5):
            before = {'objective':self.ObjVal,'fingerprint':int(self.Fingerprint),
                      'ConstrVio':self.ConstrVio,'BoundVio':self.BoundVio}
            self.Params.Crossover=1
            self.Params.FeasibilityTol=1e-8
            self.Params.OptimalityTol=1e-8
            self.Params.BarConvTol=1e-10
            self.Params.TimeLimit=3600
            OriginalModel.optimize(self, *args, **kwargs)
            polishing = []
            if self.Status == 2 and (self.ConstrVio > 1e-7 or self.BoundVio > 1e-7):
                for scale in (1, 2):
                    self.Params.Method = 1
                    self.Params.ScaleFlag = scale
                    self.Params.FeasibilityTol = 1e-9
                    self.Params.OptimalityTol = 1e-9
                    self.Params.TimeLimit = 1800
                    OriginalModel.optimize(self, *args, **kwargs)
                    polishing.append({'method': 1, 'scale_flag': scale, 'Status': self.Status,
                        'ConstrVio': self.ConstrVio if self.SolCount else None,
                        'BoundVio': self.BoundVio if self.SolCount else None})
                    if self.Status == 2 and self.ConstrVio <= 1e-7 and self.BoundVio <= 1e-7:
                        break
            recovery = {'before':before,'same_model_fingerprint':self.Fingerprint==before['fingerprint'],
                        'objective_difference':self.ObjVal-before['objective'] if self.SolCount else None,
                        'same_matrix_polishing':polishing}
            recovery['after']={'Status':self.Status,'SolCount':self.SolCount,
                'ConstrVio':self.ConstrVio if self.SolCount else None,
                'BoundVio':self.BoundVio if self.SolCount else None}
            if (self.Status!=2 or self.ConstrVio>1e-7 or self.BoundVio>1e-7
                    or not recovery['same_model_fingerprint']
                    or abs(recovery['objective_difference'])>1e-5*max(1.,abs(before['objective']))):
                (OUT/'numerical_recovery_failed.json').write_text(json.dumps(recovery,indent=2))
                raise RuntimeError('Within-path same-model numerical recovery needs review')
        context = inspect.currentframe().f_back.f_globals
        years = [int(year) for year in context.get('years', [])]
        record = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'years': years}
        record['within_path_numeric_recovery'] = recovery
        for name in ('Status', 'SolCount', 'ObjVal', 'ObjBound', 'Runtime', 'ConstrVio',
                     'BoundVio', 'DualVio', 'ComplVio', 'NumVars', 'NumConstrs', 'Fingerprint'):
            try:
                record[name] = getattr(self, name)
            except (AttributeError, gp.GurobiError):
                record[name] = None
        if self.Status == 2:
            record['objective_expression_value'] = self.getObjective().getValue()
            try:
                index = int(self.ConstrVioIndex)
                if index >= 0:
                    constraint = self.getConstrs()[index]
                    row = self.getRow(constraint)
                    lhs, rhs = row.getValue(), constraint.RHS
                    record['worst_constraint'] = {'name': constraint.ConstrName, 'sense': constraint.Sense,
                                                   'lhs': lhs, 'rhs': rhs,
                                                   'relative_residual_to_lhs_rhs': abs(lhs - rhs) / max(1, abs(lhs), abs(rhs))}
            except (AttributeError, gp.GurobiError):
                pass
        label = '_'.join(str(year) for year in years)
        (OUT / ('quality_' + label + '.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
        if self.Status != 2:
            raise RuntimeError('Do not accept a non-optimal legacy solve: ' + str(self.Status))


gp.Model = AuditedModel
runpy.run_path(str(PROJECT / 'main_rolling_horizon_v3.py'), run_name='__main__')

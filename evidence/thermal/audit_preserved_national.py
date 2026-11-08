"""Read-only independent capital-chain and input audit of the 42 existing windows."""
import json,sys,traceback
from pathlib import Path
R=Path('E:/Roko/remote_access')
CONTROL=R/'thermal_controls_recovery_20261108_01'
NATIONAL=R/'national_production_thermal_20261107_01'
sys.path[:0]=[str(NATIONAL),str(R/'energy_rebuild_20261019/runtime_packages')]

try:
    from audit_national_completion import audit
    cases=('average','temperature_cold','temperature_hot','wind_low','wind_high','solar_low','solar_high')
    results=[audit(NATIONAL,c) for c in cases]
    if any(r['status']!='verified_prefix' or r['windows']!=6 for r in results):
        raise ValueError('An existing complete path did not pass its independent audit')
    report=dict(status='verified',windows=42,cases=7,national_optimization_calls=0,evidence=results)
    (CONTROL/'preserved_national_audit.json').write_text(json.dumps(report,indent=2),encoding='utf8')
except BaseException:
    (CONTROL/'preserved_national_audit_error.json').write_text(json.dumps(dict(error=traceback.format_exc()),indent=2),encoding='utf8')
    raise

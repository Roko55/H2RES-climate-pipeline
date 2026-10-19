"""Verify installed adapter import from a separate project with its own scripts."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory() as folder:
    scripts = Path(folder)/"scripts"
    scripts.mkdir()
    (scripts/"__init__.py").write_text('raise RuntimeError("Unrelated scripts package was imported")\n')
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    code = (
        "import numpy as np; import h2res_interface as api; "
        "assert api.thermal.__name__ == 'h2res_climate.temperature_coupling'; "
        "values=api.availability_parameters({'external_wind':np.full(8760,.3)},2030); "
        "assert len(values)==8760 and values['external_wind',1,2030]==.3; "
        "print('Installed adapter works outside checkout with unrelated scripts package')"
    )
    subprocess.run([sys.executable, "-c", code], cwd=folder, env=environment, check=True)

"""Apply a selected V6.2 temperature schedule without invoking H2RES."""
from pathlib import Path
import json,sys
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from h2res_interface import couple_temperature_v62

def run(member=65,year=2030):
    data=ROOT/'data/weather_energy';m=json.loads((data/'thermal_manifest.json').read_bytes())
    temp=np.load(ROOT/'profiles/accepted.npy',allow_pickle=False)[member,:,2]
    heat={(unit,p,year):total/8760 for unit,total in m['annual_targets'][str(year)].items()
          if unit in ('general_demand','TE-TOZagreb') for p in range(1,8761)}
    cooling={(u,p,year):0. for u in ('general_cooling','TE-TOZagreb') for p in range(1,8761)}
    cop={('ATW_HP',p,year):3. for p in range(1,8761)}
    result=couple_temperature_v62(temp,year,heat_demand=heat,cooling_demand=cooling,
        hp_cop_period=cop,data_directory=data)
    return dict(temperature_member=member,year=year,heat_MWh=sum(result['heat_demand'].values()),
        household_cooling_electric_MWh=float(result['household_cooling_electric_mwh'].sum()),
        minimum_COP=min(result['hp_cop_period'].values()),maximum_COP=max(result['hp_cop_period'].values()),
        optimizer_called=False)

if __name__=='__main__':print(json.dumps([run(m) for m in (41,65,48)],indent=2))

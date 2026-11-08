"""Physical and source-data contracts for the V6.2 thermal method."""
from pathlib import Path
import unittest,json
import numpy as np
import pandas as pd
from scripts.weather_energy import signals,heat_service,cooling_service,cop_curve
from h2res_interface import couple_temperature_v62
from analysis.thermal_results import load,verify_national

ROOT=Path(__file__).resolve().parents[1]

class ThermalV62Tests(unittest.TestCase):
    def test_cold_increases_space_heat_but_preserves_hot_water(self):
        normal=np.full(8760,10.);cold=np.full(8760,5.)
        reference=signals(normal)[0].sum()
        a,water=heat_service(normal,8000,2000,reference)
        b,coldwater=heat_service(cold,8000,2000,reference)
        self.assertAlmostEqual(a.sum(),8000.)
        self.assertAlmostEqual(b.sum(),13000.)
        np.testing.assert_array_equal(water,coldwater)
        self.assertAlmostEqual(water.sum(),2000.)

    def test_cooling_has_six_hour_cyclic_history_and_is_not_normalized(self):
        t=np.full(8760,20.);t[-1]=32.
        hdd,cdd=signals(t)
        self.assertAlmostEqual(cdd[0],0.)
        t[-1]=44.;_,cdd=signals(t)
        self.assertAlmostEqual(cdd[0],2.)
        warm=np.full(8760,30.)
        service=cooling_service(warm,1000.,8760.*4.)
        self.assertAlmostEqual(service.sum(),2000.)

    def test_shared_cop_can_change_annual_mean(self):
        cold=cop_curve(np.full(8760,0.),1.130404757464719)
        warm=cop_curve(np.full(8760,20.),1.130404757464719)
        self.assertLess(cold.mean(),warm.mean())
        self.assertTrue((cold>=1.2).all() and (warm<=5.5).all())

    def test_archived_adapter_matches_selected_schedules_without_input_mutation(self):
        from scripts.weather_energy_coupling import load_inputs
        directory=ROOT/'data/weather_energy';m,tables=load_inputs(str(directory.resolve()))
        ensemble=np.load(ROOT/'profiles/accepted.npy',allow_pickle=False);year=2030
        heats=[];cools=[]
        for member in (41,65,48):
            heat={(u,p,year):m['annual_targets'][str(year)][u]/8760 for u in ('general_demand','TE-TOZagreb') for p in range(1,8761)}
            cooling={(u,p,year):0. for u in ('general_cooling','TE-TOZagreb') for p in range(1,8761)}
            cop={('ATW_HP',p,year):3. for p in range(1,8761)}
            before=heat.copy()
            result=couple_temperature_v62(ensemble[member,:,2],year,heat_demand=heat,cooling_demand=cooling,
                hp_cop_period=cop,data_directory=directory)
            expected=tables[year,member]
            np.testing.assert_allclose(result['household_cooling_electric_mwh'],expected.cooling_electric_mwh,rtol=0,atol=0)
            self.assertEqual(heat,before);self.assertEqual(set(cop.values()),{3.})
            heats.append(sum(result['heat_demand'].values()));cools.append(result['household_cooling_electric_mwh'].sum())
        self.assertGreater(heats[0],heats[1]);self.assertGreater(heats[1],heats[2])
        self.assertLess(cools[0],cools[1]);self.assertLess(cools[1],cools[2])

    def test_source_audit_and_all_42_windows_agree(self):
        proof=verify_national(ROOT)
        self.assertEqual(proof['source_acceptance_hashes_verified'],42)

    def test_cold_window_cost_and_annual_cost_are_different_comparisons(self):
        data={(r['case'],r['year']):r for r in load(ROOT)}
        c,a=data['temperature_cold',2030],data['average',2030]
        self.assertLess(c['obj'],a['obj']);self.assertGreater(c['year_total'],a['year_total'])

if __name__=='__main__':unittest.main()

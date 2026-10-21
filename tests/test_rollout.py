"""Physical clock, quantile and autoregressive sampling regression checks."""
import unittest
from types import SimpleNamespace
import numpy as np
import pipeline


class ConstantBooster:
    def __init__(self, value):
        self.value = value
        self.retained_trees = set()

    def __getitem__(self, selected):
        self.retained_trees.add(selected.stop)
        return self

    def set_param(self, values):
        pass

    def inplace_predict(self, values):
        return np.full(len(values), self.value, dtype=np.float32)


class RolloutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = pipeline.core()

    def setUp(self):
        self.old = (self.g.CONFIG.latitude, self.g.CONFIG.longitude, self.g.CONFIG.solar_sites)
        self.g.CONFIG.latitude = 44.52
        self.g.CONFIG.longitude = 16.798
        self.g.CONFIG.solar_sites = ((43.788,16.001),(45.311,18.235),(45.865,16.206))

    def tearDown(self):
        self.g.CONFIG.latitude, self.g.CONFIG.longitude, self.g.CONFIG.solar_sites = self.old

    def test_utc_noon_and_hourly_support(self):
        hours = np.arange(0,24,.01)
        maximum = hours[np.argmax(self.g.calculate_solar_elevation(81,hours,45,15))]
        self.assertTrue(10.8 < maximum < 11.3)
        h = np.arange(8760)
        support = self.g.solar_interval_elevation(h//24+1,h%24)
        for lat,lon in self.g.CONFIG.solar_sites:
            point = self.g.calculate_solar_elevation(h//24+1,h%24+.5,lat,lon)
            self.assertTrue((support >= point-1e-12).all())
        self.assertTrue((self.g.solar_interval_elevation(np.arange(1,366),0)<-1).all())

    def test_crossed_quantiles_are_ordered_idempotently(self):
        result = self.g.ordered_quantiles(np.array([3.,0.]),np.array([1.,1.]),np.array([2.,2.]))
        self.assertTrue((np.diff(result,axis=0)>=0).all())
        np.testing.assert_array_equal(result,self.g.ordered_quantiles(*result))

    def test_sampling_replays_uses_best_trees_and_physical_bounds(self):
        training = np.zeros((2*8760,3))
        training[:,0] = .2
        training[:,1] = .15
        training[:,2] = 10.
        parameters = {'hourly_mean':training.reshape(2,8760,3).mean(axis=0),
            'hourly_std':np.zeros((8760,3)), 'wind_train_data':training[:,1],
            'temp_train_min':-5.,'temp_train_max':25.}
        for c in ('solar','temperature'):
            parameters.update({c+'_rho':.4,c+'_sigma_cal':1.,c+'_drift_alpha':pipeline.DRIFT_ALPHA})
        boosters = [ConstantBooster(v) for v in (.3,.1,.2)]
        estimators = {q:SimpleNamespace(best_iteration=1,get_booster=lambda b=b:b)
                      for q,b in zip((.16,.5,.84),boosters)}
        models = {'Solar':estimators,'Temperature':estimators}
        a = self.g.generate_scenarios(models,parameters,2,training,42)
        b = self.g.generate_scenarios(models,parameters,2,training,42)
        np.testing.assert_array_equal(a,b)
        dark = self.g.solar_interval_elevation(np.arange(8760)//24+1,np.arange(8760)%24)<=-1
        self.assertTrue((a[:,dark,0]==0).all())
        self.assertTrue((a[:,:,:2]>=0).all() and (a[:,:,:2]<=1).all())
        self.assertFalse(np.array_equal(a[0,:,0],a[1,:,0]))
        self.assertTrue(all(booster.retained_trees=={2} for booster in boosters))
        np.testing.assert_array_equal(a[:,:,1],self.g._generate_wind_bootstrap(training[:,1],8760,2,seed=42))


if __name__ == '__main__':
    unittest.main()

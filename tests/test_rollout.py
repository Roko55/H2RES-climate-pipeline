"""Physical clock, quantile and annual sampling regression checks."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import pipeline
from scripts import rollout_sampling as sampler


class ConstantBooster:
    def __init__(self, value):
        self.value = value
        self.iteration_ranges = set()

    def set_param(self, values):
        pass

    def inplace_predict(self, values, iteration_range):
        self.iteration_ranges.add(iteration_range)
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

    def test_common_bias_preserves_bounds_and_night_zeros(self):
        training = np.zeros((2,8760,3))
        training[:,:,2] = np.linspace(-5,25,8760)
        solar = np.tile([0.,.1,.3,.4],(8,2190))
        bias = sampler.fit_bias(solar,training,0,.25)
        corrected = sampler.apply_bias(solar,bias)
        self.assertAlmostEqual(corrected.mean(),.25,places=7)
        self.assertTrue((corrected[:,::4]==0).all())
        self.assertTrue(((corrected>=0)&(corrected<=1)).all())
        temperature = np.full((8,8760),10.)
        bias = sampler.fit_bias(temperature,training,2,12.)
        self.assertAlmostEqual(sampler.apply_bias(temperature,bias).mean(),12.,places=7)
        self.assertEqual(bias['bounds'],[-15.,35.])

    def test_sampling_replays_uses_best_trees_and_channel_streams_differ(self):
        training = np.zeros((2,8760,3))
        training[:,:,0] = .2
        training[:,:,2] = 10.
        parameters = {'hourly_mean':training.mean(axis=0),'temp_train_min':-5.,'temp_train_max':25.,
            'solar_rho':.4,'solar_sigma_cal':1.,'temperature_rho':.4,'temperature_sigma_cal':1.}
        # Deliberately crossed predictions exercise rearrangement in sampling.
        boosters = [ConstantBooster(v) for v in (.3,.1,.2)]
        estimators = {q:SimpleNamespace(best_iteration=1,get_booster=lambda b=b:b)
                      for q,b in zip((.16,.5,.84),boosters)}
        models = {'Solar':estimators,'Temperature':estimators}
        options = [{'drift_alpha':0.,'noise_multiplier':1.}]
        a = sampler.generate_candidates(self.g,models,parameters,training,'Solar',options,2,42)
        b = sampler.generate_candidates(self.g,models,parameters,training,'Solar',options,2,42)
        c = sampler.generate_candidates(self.g,models,parameters,training,'Temperature',options,2,42)
        np.testing.assert_array_equal(a,b)
        hours = np.arange(8760)
        dark = self.g.solar_interval_elevation(hours//24+1,hours%24)<=-1
        self.assertTrue((a[:,:,dark]==0).all())
        self.assertTrue((a>=0).all() and (a<=1).all())
        self.assertFalse(np.array_equal(a[0,0],a[0,1]))
        self.assertFalse(np.array_equal(a[0,0,~dark],c[0,0,~dark]))
        self.assertTrue(all(booster.iteration_ranges=={(0,2)} for booster in boosters))

    def test_generation_requires_complete_frozen_settings(self):
        with self.assertRaisesRegex(ValueError,'Training history'):
            sampler.generate_profiles(self.g,{}, {},None,{},1,42)
        with self.assertRaisesRegex(ValueError,'Both channels'):
            sampler.generate_profiles(self.g,{}, {},np.zeros((1,8760,3)),{},1,42)

    def test_acceptance_rejects_collapsed_spread(self):
        protocol = {'validation_acceptance':{
            **{key+suffix:value for key in ('coverage95','coverage80','ramp95_ratio','calendar_sd_ratio','annual_sd_ratio')
               for suffix,value in (('_min',.5),('_max',2.))},
            'mean_solar_bias_CF_max':.01,'mean_temperature_bias_C_max':.5}}
        good = {key:1. for key in ('coverage95','coverage80','ramp95_ratio','calendar_sd_ratio','annual_sd_ratio')}
        good['mean_bias'] = 0.
        self.assertTrue(sampler.accepted(good,protocol,0)[0])
        bad = dict(good,calendar_sd_ratio=.05)
        self.assertFalse(sampler.accepted(bad,protocol,0)[0])


if __name__ == '__main__':
    unittest.main()

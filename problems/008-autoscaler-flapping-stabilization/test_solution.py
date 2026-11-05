import unittest

from solution import (NaiveScaler, ScalerConfig, StabilizedScaler, noisy_load, raw_recommendation,
                      simulate)

STEP = 15                      # seconds between autoscaler evaluations
HOUR = 3600


class ReproduceFlapping(unittest.TestCase):
    def test_naive_scaler_sawtooths_on_noise(self):
        load = noisy_load(HOUR, STEP, base=6.0, noise=0.25, seed=42)
        res = simulate(NaiveScaler(ScalerConfig(), replicas=10), load, STEP)
        self.assertGreater(res["changes"], 100)          # changes most ticks of 240
        self.assertGreater(res["pod_starts"], 100)


class Stabilization(unittest.TestCase):
    def test_noise_no_longer_causes_churn(self):
        cfg = ScalerConfig()
        load = noisy_load(HOUR, STEP, base=6.0, noise=0.25, seed=42)
        naive = simulate(NaiveScaler(cfg, 10), load, STEP)
        fixed = simulate(StabilizedScaler(cfg, 10), load, STEP)
        self.assertLessEqual(fixed["changes"], 5)
        self.assertLessEqual(fixed["overloaded_ticks"], naive["overloaded_ticks"])
        self.assertLess(fixed["pod_starts"], naive["pod_starts"] / 10)

    def test_real_spike_scales_up_on_next_tick(self):
        s = StabilizedScaler(ScalerConfig(), replicas=10)
        s.step(0, 6.0)
        self.assertEqual(s.step(15, 18.0), 20)            # ceil(18/0.6) = 30, capped at +100%
        self.assertEqual(s.step(30, 18.0), 30)

    def test_scale_up_step_limit_uses_larger_of_percent_and_pods(self):
        s = StabilizedScaler(ScalerConfig(min_replicas=1), replicas=2)
        self.assertEqual(s.step(0, 6.0), 6)               # +100% would be 4; +4 pods wins

    def test_scale_down_waits_for_full_window(self):
        cfg = ScalerConfig()
        s = StabilizedScaler(cfg, replicas=10)
        s.step(0, 6.0)
        t = 15
        while t < 300:
            self.assertEqual(s.step(t, 3.0), 10)           # dip: hold capacity
            t += 15
        self.assertEqual(s.step(300, 3.0), 5)              # low for 5 minutes: shrink

    def test_brief_dip_inside_window_never_scales_down(self):
        s = StabilizedScaler(ScalerConfig(), replicas=10)
        demand = [6.0] * 8 + [2.0] * 4 + [6.0] * 8
        seen = [s.step(i * STEP, d) for i, d in enumerate(demand)]
        self.assertEqual(set(seen), {10})

    def test_dip_on_first_tick_after_start_does_not_scale_down(self):
        s = StabilizedScaler(ScalerConfig(), replicas=10)
        seen = [s.step(t, d) for t, d in [(0, 3.0), (15, 6.0), (30, 6.0)]]
        self.assertEqual(seen, [10, 10, 10])

    def test_low_demand_from_start_shrinks_after_full_window(self):
        s = StabilizedScaler(ScalerConfig(), replicas=10)
        for t in range(0, 300, STEP):
            self.assertEqual(s.step(t, 3.0), 10)
        self.assertEqual(s.step(300, 3.0), 5)

    def test_tolerance_band(self):
        s = StabilizedScaler(ScalerConfig(), replicas=10)
        self.assertEqual(s.step(0, 6.5), 10)               # ratio 1.083: inside ±10%
        self.assertEqual(s.step(15, 6.7), 12)              # ratio 1.117: outside

    def test_bounds(self):
        cfg = ScalerConfig(min_replicas=3, max_replicas=12)
        s = StabilizedScaler(cfg, replicas=10)
        self.assertEqual(s.step(0, 100.0), 12)
        s2 = StabilizedScaler(cfg, replicas=3)
        for t in range(0, 900, 15):
            r = s2.step(t, 0.0)
        self.assertEqual(r, 3)

    def test_raw_recommendation_validation(self):
        self.assertEqual(raw_recommendation(10, 6.0, ScalerConfig()), 10)
        with self.assertRaises(ValueError):
            raw_recommendation(0, 1.0, ScalerConfig())


if __name__ == "__main__":
    unittest.main()

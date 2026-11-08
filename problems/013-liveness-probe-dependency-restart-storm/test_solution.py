import unittest

from solution import (World, check_probe_config, local_liveness, naive_liveness,
                      simulate, slow_db)

PODS, DUR = 20, 1_800
SLOW_START, SLOW_END = 100, 700          # ten-minute database slowdown
STARTUP = 45                             # JVM start + cache warm-up


def run(probe, **kw):
    world = World(slow_db(SLOW_START, SLOW_END), **kw)
    return simulate(PODS, DUR, world, probe, startup_seconds=STARTUP,
                    recovered_at=SLOW_END)


def zero_gaps(available):
    """Lengths of consecutive runs of seconds with no running pod."""
    gaps, current = [], 0
    for up in available:
        if up == 0:
            current += 1
        elif current:
            gaps.append(current)
            current = 0
    return gaps


class ReproduceRestartStorm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run(naive_liveness)

    def test_every_pod_restarts(self):
        self.assertGreaterEqual(self.res.restarts, PODS)
        self.assertEqual(self.res.restarts, 100)

    def test_whole_fleet_down(self):
        self.assertGreater(self.res.seconds_at_zero(), 0)
        self.assertEqual(self.res.seconds_at_zero(), 490)

    def test_outage_outlasts_database_by_over_a_minute(self):
        delay = self.res.first_full_after - SLOW_END
        self.assertGreater(delay, 60)
        self.assertEqual(delay, 64)

    def test_no_pods_running_for_a_while_after_database_recovered(self):
        down_after = sum(1 for t, up in enumerate(self.res.available)
                         if t >= SLOW_END and up == 0)
        self.assertEqual(down_after, 55)


class LocalLiveness(unittest.TestCase):
    def test_no_restarts_and_no_capacity_loss(self):
        res = run(local_liveness)
        self.assertEqual(res.restarts, 0)
        self.assertEqual(min(res.available), PODS)
        self.assertEqual(res.first_full_after, SLOW_END)

    def test_hung_pod_is_still_restarted(self):
        world = World(slow_db(SLOW_START, SLOW_END), hung_pods={3})
        res = simulate(PODS, 200, world, local_liveness)
        self.assertGreaterEqual(res.restarts, 1)
        self.assertEqual(min(res.available), PODS - 1)    # only the hung pod

    def test_short_blip_below_threshold_is_harmless_even_naive(self):
        world = World(slow_db(100, 115))
        res = simulate(PODS, 400, world, naive_liveness)
        self.assertEqual(res.restarts, 0)


class KubeletModel(unittest.TestCase):
    def test_crash_loop_backoff_grows(self):
        world = World(lambda t: 0.0, hung_pods={0})
        res = simulate(1, 2_000, world, local_liveness, startup_seconds=5)
        # restarts get rarer as back-off doubles to the 300 s cap
        self.assertLess(res.restarts, 15)
        self.assertGreater(res.restarts, 5)

    def test_backoff_sequence_is_10_20_40_doubling_to_300_cap(self):
        world = World(lambda t: 0.0, hung_pods={0})
        startup = 5
        res = simulate(1, 1_500, world, local_liveness, startup_seconds=startup)
        backoffs = [gap - startup for gap in zero_gaps(res.available)]
        self.assertEqual(backoffs[:7], [10, 20, 40, 80, 160, 300, 300])

    def test_restart_after_failure_threshold(self):
        world = World(lambda t: 0.0, hung_pods={0})
        res = simulate(1, 31, world, local_liveness)
        # probes at t=0,10,20 fail -> restart at t=20
        self.assertEqual(res.available[19], 1)
        self.assertEqual(res.available[20], 0)


class ProbeLint(unittest.TestCase):
    def test_flags_dependency_in_liveness(self):
        problems = check_probe_config(["process", "postgres"], ["process", "postgres"],
                                      {"postgres", "redis"})
        self.assertEqual(len(problems), 2)
        self.assertIn("postgres", problems[0])

    def test_good_config(self):
        self.assertEqual(check_probe_config(["process"], ["process", "cache_warm"],
                                            {"postgres"}), [])


if __name__ == "__main__":
    unittest.main()

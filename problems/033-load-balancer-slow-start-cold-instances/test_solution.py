import unittest
from collections import Counter

from solution import (DEFAULT_WEIGHT_FLOOR, FULL_WEIGHT, FakeClock, NaiveBalancer,
                      SlowStartBalancer, simulate_scale_out, slow_start_weight)

WARM_INSTANCES = 9
REQUESTS_PER_SECOND = 1_000
WARM_CAPACITY_RPS = 150      # one fully warm instance
COLD_FRACTION = 0.1          # a just-started instance serves 10% of that
WARMUP_SECONDS = 60
SIMULATED_SECONDS = 90


def run_scale_out(balancer_class):
    """Add one cold instance to nine warm ones and return per-second stats."""
    clock = FakeClock()
    balancer = balancer_class(clock)
    return simulate_scale_out(balancer, clock, warm_instances=WARM_INSTANCES,
                              requests_per_second=REQUESTS_PER_SECOND,
                              seconds=SIMULATED_SECONDS,
                              warm_capacity_rps=WARM_CAPACITY_RPS,
                              cold_fraction=COLD_FRACTION, warmup_seconds=WARMUP_SECONDS)


def shares(balancer, picks):
    """Count which backend each of ``picks`` requests went to."""
    return Counter(balancer.pick() for _ in range(picks))


class ReproduceProblem(unittest.TestCase):
    def test_cold_instance_gets_a_full_share_immediately(self):
        stats = run_scale_out(NaiveBalancer)
        self.assertEqual(stats[0].new_instance_requests, 100)    # 10% of traffic at second 0
        self.assertEqual(stats[0].timeouts, 85)                  # but it can serve only 15/s

    def test_scale_out_causes_timeouts_until_the_instance_warms_up(self):
        stats = run_scale_out(NaiveBalancer)
        self.assertEqual(sum(s.timeouts for s in stats), 1_663)
        self.assertEqual(sum(1 for s in stats if s.timeouts), 38)


class FixSlowStart(unittest.TestCase):
    def test_cold_instance_starts_with_a_small_share(self):
        stats = run_scale_out(SlowStartBalancer)
        self.assertEqual(stats[0].new_instance_requests, 11)     # 0.1 / 9.1 of traffic

    def test_share_ramps_to_full_by_end_of_warmup(self):
        stats = run_scale_out(SlowStartBalancer)
        self.assertEqual(stats[30].new_instance_requests, 58)
        self.assertEqual(stats[WARMUP_SECONDS].new_instance_requests, 100)

    def test_scale_out_causes_no_timeouts(self):
        stats = run_scale_out(SlowStartBalancer)
        self.assertEqual(sum(s.timeouts for s in stats), 0)

    def test_warm_instances_absorb_the_difference_within_capacity(self):
        clock = FakeClock()
        balancer = SlowStartBalancer(clock, warmup_seconds=WARMUP_SECONDS)
        for i in range(WARM_INSTANCES):
            balancer.add(f"warm-{i}")
        clock.advance(WARMUP_SECONDS)
        balancer.add("new-1")
        counts = shares(balancer, REQUESTS_PER_SECOND)
        busiest_warm = max(n for name, n in counts.items() if name != "new-1")
        self.assertLessEqual(busiest_warm, WARM_CAPACITY_RPS)

    def test_weight_is_linear_between_floor_and_full(self):
        self.assertEqual(slow_start_weight(0, 60, 0.1), 0.1)
        self.assertAlmostEqual(slow_start_weight(30, 60, 0.1), 0.55)
        self.assertEqual(slow_start_weight(60, 60, 0.1), FULL_WEIGHT)
        self.assertEqual(slow_start_weight(600, 60, 0.1), FULL_WEIGHT)

    def test_zero_warmup_means_full_weight_immediately(self):
        self.assertEqual(slow_start_weight(0, 0, 0.1), FULL_WEIGHT)

    def test_restarted_instance_ramps_again(self):
        clock = FakeClock()
        balancer = SlowStartBalancer(clock)
        balancer.add("a")
        balancer.add("b")
        clock.advance(WARMUP_SECONDS)
        balancer.add("b")                     # b restarted: cold again
        backend_b = balancer.backends["b"]
        self.assertEqual(balancer.weight(backend_b), DEFAULT_WEIGHT_FLOOR)

    def test_whole_fleet_starting_together_shares_traffic_evenly(self):
        clock = FakeClock()
        balancer = SlowStartBalancer(clock)
        for name in ("a", "b", "c"):
            balancer.add(name)
        self.assertEqual(shares(balancer, 300), Counter({"a": 100, "b": 100, "c": 100}))

    def test_picks_are_deterministic(self):
        def sequence():
            clock = FakeClock()
            balancer = SlowStartBalancer(clock)
            balancer.add("a")
            clock.advance(WARMUP_SECONDS)
            balancer.add("b")
            return [balancer.pick() for _ in range(50)]
        self.assertEqual(sequence(), sequence())

    def test_zero_floor_is_rejected(self):
        with self.assertRaises(ValueError):
            SlowStartBalancer(FakeClock(), floor=0.0)

    def test_pick_with_no_backends_raises(self):
        with self.assertRaises(RuntimeError):
            SlowStartBalancer(FakeClock()).pick()


if __name__ == "__main__":
    unittest.main()

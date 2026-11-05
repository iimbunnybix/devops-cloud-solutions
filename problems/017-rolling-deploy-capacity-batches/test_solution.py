import unittest

from solution import (MAX_WAIT_STEPS, NEW_VERSION, Instance, capacity_aware_planner,
                      instances_needed, make_fleet, naive_batch_size, naive_planner,
                      run_rollout, safe_batch_size)

FLEET_SIZE = 40
PER_INSTANCE_RPS = 250.0          # each instance tops out at 250 requests/second
PEAK_RPS = 8000.0                 # evening peak: needs 32 instances at 100%
OFF_PEAK_RPS = 5000.0
MAX_BATCH = 5


def always_healthy(instance: Instance) -> bool:
    return True


class FailsOnNthInstance:
    """Fake health check: the Nth new instance never becomes ready (a bad build)."""

    def __init__(self, failing_call: int) -> None:
        self.failing_call = failing_call
        self.calls = 0

    def __call__(self, instance: Instance) -> bool:
        self.calls += 1
        return self.calls != self.failing_call


def rollout(planner, traffic: list[float], health=always_healthy):
    fleet = make_fleet(FLEET_SIZE)
    return fleet, run_rollout(fleet, traffic, PER_INSTANCE_RPS, planner, health)


class ReproduceProblem(unittest.TestCase):
    def test_fixed_25_percent_batch_overloads_the_fleet_at_peak(self):
        _, result = rollout(naive_planner(), [PEAK_RPS] * 10)
        self.assertEqual(naive_batch_size(FLEET_SIZE), 10)
        # 30 instances x 250 rps = 7,500 rps < 8,000 rps of traffic, on every batch
        self.assertEqual(result.steps[0].serving_capacity_rps, 7500.0)
        self.assertEqual(result.overloaded_steps, 4)

    def test_same_rollout_is_safe_off_peak(self):
        _, result = rollout(naive_planner(), [OFF_PEAK_RPS] * 10)
        self.assertEqual(result.overloaded_steps, 0)


class FixCapacityAwareBatches(unittest.TestCase):
    def test_instances_needed_keeps_headroom(self):
        # 8,000 / (250 x 0.8) = 40 instances
        self.assertEqual(instances_needed(PEAK_RPS, PER_INSTANCE_RPS), 40)

    def test_no_batch_when_there_is_no_spare_capacity(self):
        self.assertEqual(safe_batch_size(40, PEAK_RPS, PER_INSTANCE_RPS, MAX_BATCH), 0)

    def test_batch_is_limited_by_spare_capacity(self):
        # 7,000 rps needs 35 instances, so only 5 of 40 may go
        self.assertEqual(safe_batch_size(40, 7000.0, PER_INSTANCE_RPS, 10), 5)

    def test_batch_is_limited_by_max_batch(self):
        self.assertEqual(safe_batch_size(40, 1000.0, PER_INSTANCE_RPS, MAX_BATCH), MAX_BATCH)

    def test_rollout_waits_through_peak_then_completes_without_overload(self):
        traffic = [PEAK_RPS] * 3 + [OFF_PEAK_RPS] * 10
        fleet, result = rollout(capacity_aware_planner(PER_INSTANCE_RPS, MAX_BATCH), traffic)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.overloaded_steps, 0)
        self.assertTrue(all(instance.version == NEW_VERSION for instance in fleet))

    def test_rollout_aborts_if_peak_never_ends(self):
        traffic = [PEAK_RPS] * (MAX_WAIT_STEPS + 2)
        fleet, result = rollout(capacity_aware_planner(PER_INSTANCE_RPS, MAX_BATCH), traffic)
        self.assertEqual(result.status, "aborted_no_capacity")
        self.assertEqual(result.steps, [])

    def test_unhealthy_new_instance_halts_the_rollout(self):
        health = FailsOnNthInstance(failing_call=7)
        fleet, result = rollout(capacity_aware_planner(PER_INSTANCE_RPS, MAX_BATCH),
                                [OFF_PEAK_RPS] * 10, health)
        self.assertEqual(result.status, "halted_unhealthy")
        upgraded = sum(1 for instance in fleet if instance.version == NEW_VERSION)
        self.assertEqual(upgraded, 7)            # stopped inside the second batch

    def test_last_batch_is_smaller_than_max_when_few_old_remain(self):
        fleet, result = rollout(capacity_aware_planner(PER_INSTANCE_RPS, 15), [1000.0] * 5)
        self.assertEqual([step.taken_down for step in result.steps], [15, 15, 10])
        self.assertEqual(result.status, "completed")


if __name__ == "__main__":
    unittest.main()

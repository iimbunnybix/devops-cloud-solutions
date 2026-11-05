import unittest

from solution import (HEALTHY_AFTER_SUCCESSES, UNHEALTHY_AFTER_FAILURES, BackendHealth,
                      LoadBalancer, naive_routable_backends)

BACKEND_COUNT = 10


def backend_names() -> list[str]:
    return [f"web-{index}" for index in range(BACKEND_COUNT)]


def fail_checks(balancer: LoadBalancer, names: list[str], rounds: int) -> None:
    for _round in range(rounds):
        for name in names:
            balancer.record_check(name, check_passed=False)


def pass_checks(balancer: LoadBalancer, names: list[str], rounds: int) -> None:
    for _round in range(rounds):
        for name in names:
            balancer.record_check(name, check_passed=True)


class ReproduceProblem(unittest.TestCase):
    def test_shared_dependency_blip_leaves_naive_pool_empty(self):
        backends = [BackendHealth(name) for name in backend_names()]
        for backend in backends:
            backend.record(check_passed=False)    # session cache fails over
        self.assertEqual(naive_routable_backends(backends), [])

    def test_naive_rule_ejects_on_a_single_failed_check(self):
        backends = [BackendHealth(name) for name in backend_names()]
        backends[0].record(check_passed=False)
        self.assertNotIn("web-0", naive_routable_backends(backends))


class FixPanicThreshold(unittest.TestCase):
    def setUp(self) -> None:
        self.balancer = LoadBalancer(backend_names())

    def test_one_failed_check_does_not_eject_a_backend(self):
        fail_checks(self.balancer, ["web-0"], rounds=UNHEALTHY_AFTER_FAILURES - 1)
        self.assertIn("web-0", self.balancer.routable_backends())

    def test_truly_dead_backend_is_ejected(self):
        fail_checks(self.balancer, ["web-0"], rounds=UNHEALTHY_AFTER_FAILURES)
        self.assertNotIn("web-0", self.balancer.routable_backends())
        self.assertFalse(self.balancer.in_panic_mode())

    def test_shared_blip_keeps_routing_to_all_backends(self):
        fail_checks(self.balancer, backend_names(), rounds=10)
        self.assertTrue(self.balancer.in_panic_mode())
        self.assertEqual(sorted(self.balancer.routable_backends()), sorted(backend_names()))

    def test_exactly_half_healthy_is_not_panic(self):
        fail_checks(self.balancer, backend_names()[:5], rounds=UNHEALTHY_AFTER_FAILURES)
        self.assertFalse(self.balancer.in_panic_mode())
        self.assertEqual(len(self.balancer.routable_backends()), 5)

    def test_below_half_healthy_enters_panic(self):
        fail_checks(self.balancer, backend_names()[:6], rounds=UNHEALTHY_AFTER_FAILURES)
        self.assertTrue(self.balancer.in_panic_mode())

    def test_recovery_needs_consecutive_successes(self):
        fail_checks(self.balancer, ["web-0"], rounds=UNHEALTHY_AFTER_FAILURES)
        pass_checks(self.balancer, ["web-0"], rounds=HEALTHY_AFTER_SUCCESSES - 1)
        self.assertNotIn("web-0", self.balancer.routable_backends())
        pass_checks(self.balancer, ["web-0"], rounds=1)
        self.assertIn("web-0", self.balancer.routable_backends())

    def test_panic_mode_ends_when_backends_recover(self):
        fail_checks(self.balancer, backend_names(), rounds=UNHEALTHY_AFTER_FAILURES)
        pass_checks(self.balancer, backend_names(), rounds=HEALTHY_AFTER_SUCCESSES)
        self.assertFalse(self.balancer.in_panic_mode())

    def test_round_robin_spreads_requests_over_healthy_backends(self):
        fail_checks(self.balancer, ["web-0"], rounds=UNHEALTHY_AFTER_FAILURES)
        picks = [self.balancer.pick_backend() for _ in range(90)]
        self.assertNotIn("web-0", picks)
        for name in backend_names()[1:]:
            self.assertEqual(picks.count(name), 10)

    def test_empty_backend_list_is_rejected(self):
        with self.assertRaises(ValueError):
            LoadBalancer([])


if __name__ == "__main__":
    unittest.main()

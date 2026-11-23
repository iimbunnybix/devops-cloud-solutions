import unittest

from solution import (HTTP_BAD_GATEWAY, HTTP_OK, HTTP_UNAVAILABLE, UNHEALTHY_THRESHOLD, Instance,
                      LoadBalancer, app_health_check, count_errors, naive_health_check)

INSTANCE_COUNT = 10
INTERVALS = 30                 # 5 minutes of 10-second check intervals
REQUESTS_PER_INTERVAL = 600    # 60 requests per second
CRASHED = 3


def fleet_with_crashed_app():
    instances = [Instance(f"web-{index}") for index in range(INSTANCE_COUNT)]
    instances[CRASHED].app_up = False
    return instances


def in_rotation(balancer, name):
    return balancer.states[name].in_rotation


class ReproduceProblem(unittest.TestCase):
    def test_static_file_check_passes_while_the_app_is_down(self):
        crashed = Instance("web-0", app_up=False)
        self.assertEqual(naive_health_check(crashed), HTTP_OK)

    def test_crashed_instance_stays_in_rotation_forever(self):
        balancer = LoadBalancer(fleet_with_crashed_app(), naive_health_check)
        count_errors(balancer, INTERVALS, REQUESTS_PER_INTERVAL)
        self.assertTrue(in_rotation(balancer, "web-3"))

    def test_one_crashed_app_fails_a_tenth_of_all_requests(self):
        balancer = LoadBalancer(fleet_with_crashed_app(), naive_health_check)
        errors = count_errors(balancer, INTERVALS, REQUESTS_PER_INTERVAL)
        self.assertEqual(errors, 1_800)
        self.assertEqual(errors / (INTERVALS * REQUESTS_PER_INTERVAL), 0.1)


class FixAppHealthCheck(unittest.TestCase):
    def test_crashed_app_fails_the_check(self):
        self.assertEqual(app_health_check(Instance("web-0", app_up=False)), HTTP_BAD_GATEWAY)

    def test_crashed_instance_is_removed_after_the_threshold(self):
        balancer = LoadBalancer(fleet_with_crashed_app(), app_health_check)
        for _ in range(UNHEALTHY_THRESHOLD - 1):
            balancer.run_health_checks()
        self.assertTrue(in_rotation(balancer, "web-3"))
        balancer.run_health_checks()
        self.assertFalse(in_rotation(balancer, "web-3"))

    def test_errors_stop_once_the_instance_is_removed(self):
        balancer = LoadBalancer(fleet_with_crashed_app(), app_health_check)
        errors = count_errors(balancer, INTERVALS, REQUESTS_PER_INTERVAL)
        self.assertEqual(errors, 120)

    def test_recovered_instance_returns_after_two_good_checks(self):
        instances = fleet_with_crashed_app()
        balancer = LoadBalancer(instances, app_health_check)
        count_errors(balancer, 5, REQUESTS_PER_INTERVAL)
        instances[CRASHED].app_up = True
        balancer.run_health_checks()
        self.assertFalse(in_rotation(balancer, "web-3"))
        balancer.run_health_checks()
        self.assertTrue(in_rotation(balancer, "web-3"))

    def test_single_failed_check_does_not_remove_an_instance(self):
        instances = [Instance("web-0"), Instance("web-1")]
        balancer = LoadBalancer(instances, app_health_check)
        instances[0].app_up = False
        balancer.run_health_checks()
        instances[0].app_up = True
        balancer.run_health_checks()
        self.assertTrue(in_rotation(balancer, "web-0"))


class FixReadiness(unittest.TestCase):
    def test_instance_still_warming_up_reports_unavailable(self):
        self.assertEqual(app_health_check(Instance("web-0", warmed_up=False)), HTTP_UNAVAILABLE)

    def test_draining_instance_reports_unavailable_before_it_stops(self):
        self.assertEqual(app_health_check(Instance("web-0", draining=True)), HTTP_UNAVAILABLE)

    def test_every_instance_failing_fails_open_instead_of_dropping_all_traffic(self):
        instances = [Instance("web-0", draining=True), Instance("web-1", draining=True)]
        balancer = LoadBalancer(instances, app_health_check)
        for _ in range(UNHEALTHY_THRESHOLD):
            balancer.run_health_checks()
        self.assertEqual(len(balancer.targets()), 2)

    def test_nginx_down_fails_both_checks(self):
        instance = Instance("web-0", nginx_up=False)
        self.assertNotEqual(naive_health_check(instance), HTTP_OK)
        self.assertNotEqual(app_health_check(instance), HTTP_OK)


if __name__ == "__main__":
    unittest.main()

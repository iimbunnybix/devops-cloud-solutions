import unittest

from solution import (Request, availability_by_route, is_synthetic, naive_availability,
                      slo_breached, user_availability)

HEALTH_CHECKS_PER_HOUR = 90_000
BROWSE_REQUESTS = 9_000
CHECKOUT_REQUESTS = 1_000
FAILED_CHECKOUTS = 500


def outage_hour() -> list[Request]:
    """One hour in which half of all checkouts fail while every health check passes."""
    log: list[Request] = []
    for _ in range(HEALTH_CHECKS_PER_HOUR):
        log.append(Request("/healthz", 200, source="lb-health-check"))
    for _ in range(BROWSE_REQUESTS):
        log.append(Request("/products", 200))
    for index in range(CHECKOUT_REQUESTS):
        status = 503 if index < FAILED_CHECKOUTS else 200
        log.append(Request("/checkout", status))
    return log


class ReproduceProblem(unittest.TestCase):
    def test_naive_sli_reads_99_5_percent_during_the_outage(self):
        self.assertEqual(naive_availability(outage_hour()).percent(), 99.5)

    def test_naive_sli_does_not_breach_the_99_percent_slo(self):
        self.assertFalse(slo_breached(naive_availability(outage_hour())))


class FixMeasureUserTrafficOnly(unittest.TestCase):
    def test_user_sli_reads_95_percent(self):
        self.assertEqual(user_availability(outage_hour()).percent(), 95.0)

    def test_user_sli_breaches_the_slo(self):
        self.assertTrue(slo_breached(user_availability(outage_hour())))

    def test_checkout_route_shows_half_failing(self):
        routes = availability_by_route(outage_hour())
        self.assertEqual(routes["/checkout"].percent(), 50.0)
        self.assertEqual(routes["/products"].percent(), 100.0)

    def test_health_check_path_is_not_listed_as_a_route(self):
        self.assertNotIn("/healthz", availability_by_route(outage_hour()))

    def test_probe_tagged_by_source_is_excluded_on_any_path(self):
        self.assertTrue(is_synthetic(Request("/", 200, source="uptime-probe")))

    def test_client_errors_do_not_count_against_availability(self):
        log = [Request("/checkout", 404), Request("/checkout", 200)]
        self.assertEqual(user_availability(log).percent(), 100.0)

    def test_failed_health_checks_do_not_count_either(self):
        log = [Request("/healthz", 503, source="lb-health-check"), Request("/products", 200)]
        self.assertEqual(user_availability(log).percent(), 100.0)

    def test_only_probe_traffic_means_no_data_not_100_percent(self):
        log = [Request("/healthz", 200, source="lb-health-check")]
        self.assertIsNone(user_availability(log).percent())
        self.assertFalse(slo_breached(user_availability(log)))


if __name__ == "__main__":
    unittest.main()

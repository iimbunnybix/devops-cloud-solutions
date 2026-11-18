import unittest

from solution import (DEFAULT_BACKEND_KEEPALIVE_MS, LB_IDLE_TIMEOUT_MS, LoadBalancerPool,
                      bursty_low_traffic, keepalive_problems, run_traffic,
                      safe_backend_keepalive_ms)

REQUEST_MS = 50
REQUEST_COUNT = 10_000
MAX_GAP_MS = 8_000
SEED = 7


def original_pool() -> LoadBalancerPool:
    """The incident config: balancer reuses for 60 s, backend closes after 5 s."""
    return LoadBalancerPool(LB_IDLE_TIMEOUT_MS, DEFAULT_BACKEND_KEEPALIVE_MS)


def fixed_pool() -> LoadBalancerPool:
    return LoadBalancerPool(LB_IDLE_TIMEOUT_MS, safe_backend_keepalive_ms(LB_IDLE_TIMEOUT_MS))


def traffic() -> list[int]:
    return bursty_low_traffic(REQUEST_COUNT, MAX_GAP_MS, SEED)


class ReproduceProblem(unittest.TestCase):
    def test_request_sent_just_after_backend_close_gets_502(self):
        pool = original_pool()
        pool.handle_request(0, REQUEST_MS)
        # Backend closes at 50 + 5000 ms; its FIN is still on the wire 1 ms later.
        self.assertFalse(pool.handle_request(REQUEST_MS + DEFAULT_BACKEND_KEEPALIVE_MS + 1, REQUEST_MS))

    def test_request_after_fin_arrives_opens_a_new_connection(self):
        pool = original_pool()
        pool.handle_request(0, REQUEST_MS)
        self.assertTrue(pool.handle_request(REQUEST_MS + DEFAULT_BACKEND_KEEPALIVE_MS + 10, REQUEST_MS))
        self.assertEqual(pool.stats.connections_opened, 2)

    def test_low_traffic_service_sees_occasional_502s(self):
        stats = run_traffic(original_pool(), traffic(), REQUEST_MS)
        self.assertEqual(stats.bad_gateway, 4)

    def test_config_check_flags_the_original_settings(self):
        self.assertEqual(len(keepalive_problems(LB_IDLE_TIMEOUT_MS, DEFAULT_BACKEND_KEEPALIVE_MS)), 1)


class FixBackendOutlivesBalancer(unittest.TestCase):
    def test_same_traffic_has_no_502s(self):
        stats = run_traffic(fixed_pool(), traffic(), REQUEST_MS)
        self.assertEqual(stats.bad_gateway, 0)
        self.assertEqual(stats.served, REQUEST_COUNT)

    def test_connections_are_reused_instead_of_reopened(self):
        stats = run_traffic(fixed_pool(), traffic(), REQUEST_MS)
        self.assertEqual(stats.connections_opened, 63)

    def test_balancer_closes_idle_connection_before_backend_does(self):
        pool = fixed_pool()
        pool.handle_request(0, REQUEST_MS)
        self.assertTrue(pool.handle_request(REQUEST_MS + LB_IDLE_TIMEOUT_MS, REQUEST_MS))
        self.assertEqual(pool.stats.connections_opened, 2)

    def test_safe_value_is_balancer_timeout_plus_margin(self):
        self.assertEqual(safe_backend_keepalive_ms(60_000), 65_000)

    def test_config_check_passes_the_safe_value(self):
        self.assertEqual(keepalive_problems(60_000, 65_000), [])

    def test_equal_timeouts_are_still_flagged(self):
        self.assertIn("<=", keepalive_problems(60_000, 60_000)[0])

    def test_barely_longer_timeout_asks_for_a_margin(self):
        self.assertIn("safety margin", keepalive_problems(60_000, 61_000)[0])


if __name__ == "__main__":
    unittest.main()

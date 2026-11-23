import unittest

from solution import (EPHEMERAL_PORT_HIGH, EPHEMERAL_PORT_LOW, TIME_WAIT_SECONDS, EphemeralPorts,
                      KeepAlivePool, PortExhausted, connections_needed, max_sustainable_rate,
                      naive_run, pooled_run)

PORT_COUNT = EPHEMERAL_PORT_HIGH - EPHEMERAL_PORT_LOW + 1
PEAK_RATE = 600            # requests per second to the upstream at peak
QUIET_RATE = 300
RUN_SECONDS = 120
REQUEST_SECONDS = 0.02     # 20 ms per request
POOL_SIZE = 20


def tiny_ports(count=3, time_wait=10):
    return EphemeralPorts(low=1000, high=1000 + count - 1, time_wait_seconds=time_wait)


class ReproduceProblem(unittest.TestCase):
    def test_closed_ports_cannot_be_reused_during_time_wait(self):
        ports = tiny_ports(count=1)
        ports.close(ports.connect(now=0), now=0)
        with self.assertRaises(PortExhausted):
            ports.connect(now=5)

    def test_peak_rate_exhausts_ports_after_47_seconds(self):
        report = naive_run(EphemeralPorts(), PEAK_RATE, RUN_SECONDS)
        self.assertEqual(report.first_failure_second, 47)
        self.assertEqual(report.peak_ports_in_time_wait, PORT_COUNT)

    def test_over_a_fifth_of_peak_requests_fail(self):
        report = naive_run(EphemeralPorts(), PEAK_RATE, RUN_SECONDS)
        self.assertEqual(report.failed, 15_536)
        self.assertGreater(report.failed / (PEAK_RATE * RUN_SECONDS), 0.2)

    def test_quiet_rate_never_fails_which_is_why_tests_passed(self):
        report = naive_run(EphemeralPorts(), QUIET_RATE, RUN_SECONDS)
        self.assertEqual(report.failed, 0)


class FixKeepAlivePool(unittest.TestCase):
    def test_peak_rate_has_no_failures(self):
        report = pooled_run(EphemeralPorts(), PEAK_RATE, RUN_SECONDS, REQUEST_SECONDS, POOL_SIZE)
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.succeeded, PEAK_RATE * RUN_SECONDS)

    def test_ports_used_match_concurrency_not_rate(self):
        ports = EphemeralPorts()
        pooled_run(ports, PEAK_RATE, RUN_SECONDS, REQUEST_SECONDS, POOL_SIZE)
        self.assertEqual(ports.in_use, connections_needed(PEAK_RATE, REQUEST_SECONDS))
        self.assertEqual(ports.in_time_wait, 0)

    def test_pool_never_opens_more_than_its_maximum(self):
        pool = KeepAlivePool(EphemeralPorts(), max_connections=5)
        self.assertEqual(pool.ensure_open(needed=50, now=0), 5)

    def test_traffic_above_pool_capacity_is_rejected_not_queued_forever(self):
        report = pooled_run(EphemeralPorts(), PEAK_RATE, 1, REQUEST_SECONDS, max_connections=2)
        self.assertEqual(report.succeeded, 100)
        self.assertEqual(report.failed, PEAK_RATE - 100)


class FixCapacityMath(unittest.TestCase):
    def test_linux_defaults_allow_about_470_new_connections_per_second(self):
        self.assertEqual(PORT_COUNT, 28_232)
        self.assertAlmostEqual(max_sustainable_rate(PORT_COUNT, TIME_WAIT_SECONDS), 470.53, places=2)

    def test_ports_come_back_after_time_wait(self):
        ports = tiny_ports(count=1, time_wait=10)
        ports.close(ports.connect(now=0), now=0)
        self.assertEqual(ports.connect(now=10), 1000)

    def test_twenty_ms_requests_at_peak_need_twelve_connections(self):
        self.assertEqual(connections_needed(PEAK_RATE, REQUEST_SECONDS), 12)


if __name__ == "__main__":
    unittest.main()

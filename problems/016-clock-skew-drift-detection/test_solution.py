import random
import unittest

from solution import (SKEW_ALERT_THRESHOLD_S, TimeSample, drift_rate_ppm,
                      estimate_skew, naive_offset, ntp_offset, round_trip_delay)

SERVER_PROCESSING_S = 0.002
SLOW_PATH_PROBABILITY = 0.1


class FakeNetwork:
    """Builds time samples for a client whose clock is off by ``client_offset_s``.

    Positive offset means the server's clock is ahead of the client's (the
    client is behind). Delays are seeded so every run is identical.
    """

    def __init__(self, client_offset_s: float, seed: int = 7) -> None:
        self.client_offset_s = client_offset_s
        self.rng = random.Random(seed)

    def one_way_delay(self) -> float:
        """Usually 5-20 ms; sometimes a congested 300-800 ms path."""
        if self.rng.random() < SLOW_PATH_PROBABILITY:
            return self.rng.uniform(0.3, 0.8)
        return self.rng.uniform(0.005, 0.020)

    def exchange(self, true_time: float) -> TimeSample:
        outbound = self.one_way_delay()
        inbound = self.one_way_delay()
        t0 = true_time - self.client_offset_s
        t1 = true_time + outbound
        t2 = t1 + SERVER_PROCESSING_S
        t3 = t2 + inbound - self.client_offset_s
        return TimeSample(t0, t1, t2, t3)

    def samples(self, count: int, start: float = 1_000.0) -> list[TimeSample]:
        return [self.exchange(start + index) for index in range(count)]


def token_is_valid(issued_at: float, local_now: float, leeway_s: float = 60) -> bool:
    """A typical JWT 'iat' check: reject tokens issued in the future."""
    return issued_at <= local_now + leeway_s


class ReproduceProblem(unittest.TestCase):
    def test_naive_check_alerts_on_a_perfectly_synced_host(self):
        samples = FakeNetwork(client_offset_s=0.0).samples(1000)
        false_alerts = [s for s in samples if abs(naive_offset(s)) > SKEW_ALERT_THRESHOLD_S]
        self.assertEqual(len(false_alerts), 65)

    def test_three_minutes_behind_rejects_fresh_tokens(self):
        auth_server_now = 50_000.0
        host_now = auth_server_now - 180
        self.assertFalse(token_is_valid(issued_at=auth_server_now, local_now=host_now))


class FixOffsetFormula(unittest.TestCase):
    def test_symmetric_delay_cancels_out(self):
        sample = TimeSample(t0=100.0, t1=102.3, t2=102.31, t3=100.61)
        self.assertAlmostEqual(ntp_offset(sample), 2.0)
        self.assertAlmostEqual(round_trip_delay(sample), 0.6)

    def test_synced_host_never_alerts(self):
        network = FakeNetwork(client_offset_s=0.0)
        for batch in range(100):
            report = estimate_skew(network.samples(8, start=batch * 100.0))
            self.assertFalse(report.alert)

    def test_estimate_is_within_error_bound(self):
        report = estimate_skew(FakeNetwork(client_offset_s=0.0).samples(1000))
        self.assertLessEqual(abs(report.offset_s), report.error_bound_s)
        self.assertLess(report.error_bound_s, 0.015)


class FixAlerting(unittest.TestCase):
    def test_host_three_minutes_behind_alerts(self):
        report = estimate_skew(FakeNetwork(client_offset_s=180.0).samples(8))
        self.assertTrue(report.alert)
        self.assertAlmostEqual(report.offset_s, 180.0, delta=0.02)

    def test_host_ahead_alerts_too(self):
        report = estimate_skew(FakeNetwork(client_offset_s=-2.0).samples(8))
        self.assertTrue(report.alert)

    def test_skew_inside_error_bound_does_not_alert(self):
        # Offset 0.6 s but the only sample has a 0.4 s error bound.
        sample = TimeSample(t0=0.0, t1=1.0, t2=1.0, t3=0.8)
        report = estimate_skew([sample])
        self.assertAlmostEqual(report.offset_s, 0.6)
        self.assertFalse(report.alert)

    def test_no_samples_is_an_error(self):
        with self.assertRaises(ValueError):
            estimate_skew([])


class FixDriftRate(unittest.TestCase):
    def test_slow_clock_drift_rate_is_measured(self):
        seconds_per_day = 86_400
        drift_per_second = 200 / 1_000_000          # 200 ppm slow
        checks = []
        for day in range(5):
            check_time = float(day * seconds_per_day)
            network = FakeNetwork(client_offset_s=check_time * drift_per_second, seed=day)
            checks.append((check_time, estimate_skew(network.samples(8)).offset_s))
        self.assertAlmostEqual(drift_rate_ppm(checks), 200, delta=1)

    def test_drift_needs_two_checks(self):
        with self.assertRaises(ValueError):
            drift_rate_ppm([(0.0, 0.1)])

    def test_drift_needs_distinct_times(self):
        with self.assertRaises(ValueError):
            drift_rate_ppm([(5.0, 0.1), (5.0, 0.2)])


if __name__ == "__main__":
    unittest.main()

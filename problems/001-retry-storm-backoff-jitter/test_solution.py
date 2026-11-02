import random
import unittest

from solution import (CircuitBreaker, CircuitOpenError, RetryableError, backoff_delays,
                      retry, simulate_peak_load)


class Flaky:
    def __init__(self, failures, exc=RetryableError):
        self.failures, self.calls, self.exc = failures, 0, exc

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc("boom")
        return "ok"


class RetryTests(unittest.TestCase):
    def test_jitter_flattens_the_thundering_herd(self):
        synced = simulate_peak_load(400, jitter=False)
        jittered = simulate_peak_load(400, jitter=True)
        self.assertEqual(synced, 400)                     # everyone hits at once
        self.assertLess(jittered, synced / 5)

    def test_delays_stay_within_cap(self):
        ds = backoff_delays(0.1, 2.0, 20, random.Random(1))
        self.assertTrue(all(0 <= d <= 2.0 for d in ds))

    def test_succeeds_after_transient_failures(self):
        slept = []
        fn = Flaky(3)
        self.assertEqual(retry(fn, sleep=slept.append, rng=random.Random(0)), "ok")
        self.assertEqual(fn.calls, 4)
        self.assertEqual(len(slept), 3)

    def test_gives_up_after_max_attempts(self):
        fn = Flaky(100)
        with self.assertRaises(RetryableError):
            retry(fn, max_attempts=4, sleep=lambda s: None)
        self.assertEqual(fn.calls, 4)

    def test_non_retryable_errors_fail_immediately(self):
        fn = Flaky(1, exc=ValueError)
        with self.assertRaises(ValueError):
            retry(fn, sleep=lambda s: None)
        self.assertEqual(fn.calls, 1)

    def test_deadline_is_respected(self):
        now = [0.0]

        def sleep(s):
            now[0] += s

        with self.assertRaises(RetryableError):
            retry(Flaky(100), max_attempts=50, base=1, cap=10, deadline_s=15,
                  sleep=sleep, clock=lambda: now[0], rng=random.Random(3))
        self.assertLessEqual(now[0], 15)


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_then_fails_fast_then_recovers(self):
        now = [0.0]
        cb = CircuitBreaker(failure_threshold=3, reset_after_s=10, clock=lambda: now[0])
        down = Flaky(1000)
        for _ in range(3):
            with self.assertRaises(RetryableError):
                cb.call(down)
        self.assertEqual(cb.state, "OPEN")
        with self.assertRaises(CircuitOpenError):
            cb.call(down)
        self.assertEqual(down.calls, 3)                   # dependency was not hit
        now[0] = 11
        self.assertEqual(cb.call(lambda: "ok"), "ok")
        self.assertEqual(cb.state, "CLOSED")

    def test_half_open_failure_reopens(self):
        now = [0.0]
        cb = CircuitBreaker(failure_threshold=1, reset_after_s=5, clock=lambda: now[0])
        with self.assertRaises(RetryableError):
            cb.call(Flaky(5))
        now[0] = 6
        with self.assertRaises(RetryableError):
            cb.call(Flaky(5))
        self.assertEqual(cb.state, "OPEN")


if __name__ == "__main__":
    unittest.main()

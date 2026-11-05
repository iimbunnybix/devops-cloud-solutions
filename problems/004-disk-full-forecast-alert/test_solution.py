import random
import unittest

from solution import Sample, evaluate, forecast, static_threshold_alert

GB = 1024 ** 3
CAP = 100 * GB
MIN = 60


def series(start_frac, gb_per_hour, hours=6, step_min=5, noise_gb=0.0, seed=0, t0=0):
    rng = random.Random(seed)
    out = []
    for i in range(int(hours * 60 / step_min) + 1):
        t = t0 + i * step_min * MIN
        used = start_frac * CAP + gb_per_hour * GB * (t - t0) / 3600 + rng.gauss(0, noise_gb * GB)
        out.append(Sample(t, min(CAP, max(0.0, used))))
    return out


def hourly_temp_file_sawtooth(hours=6, step_min=5):
    """Steady 70% disk; each hour a job writes 10 GB over 30 min, then deletes it."""
    out = []
    for i in range(int(hours * 60 / step_min) + 1):
        t = i * step_min * MIN
        minute_of_hour = (t // MIN) % 60
        temp_bytes = 10 * GB * minute_of_hour / 30 if minute_of_hour < 30 else 0
        out.append(Sample(t, 0.70 * CAP + temp_bytes))
    return out


class DiskForecastTests(unittest.TestCase):
    def test_static_threshold_pages_on_stable_full_disk(self):
        s = series(0.91, 0.0, noise_gb=0.05, seed=1)
        self.assertTrue(static_threshold_alert(s, CAP))      # nightly false page
        self.assertIsNone(evaluate(s, CAP))                   # forecast stays quiet

    def test_static_threshold_late_on_fast_fill(self):
        # 50% -> 80% over 6h at 5 GB/h; static rule silent, outage in 4h
        s = series(0.50, 5.0, seed=2)
        self.assertFalse(static_threshold_alert(s, CAP))
        alert = evaluate(s, CAP)
        self.assertEqual(alert.severity, "page")
        self.assertAlmostEqual(forecast(s, CAP).seconds_to_full / 3600, 4.0, delta=0.05)

    def test_slow_growth_opens_ticket(self):
        s = series(0.60, 0.5, seed=3)                         # ~3 days left
        alert = evaluate(s, CAP)
        self.assertEqual(alert.severity, "ticket")

    def test_noise_does_not_break_estimate(self):
        s = series(0.50, 5.0, noise_gb=0.5, seed=4)
        hours = forecast(s, CAP).seconds_to_full / 3600
        self.assertAlmostEqual(hours, 4.0, delta=0.5)

    def test_log_rotation_drop_resets_trend(self):
        before = series(0.70, 2.0, hours=3, seed=5)
        after = series(0.40, 6.0, hours=2, seed=6, t0=before[-1].ts + 5 * MIN)
        f = forecast(before + after, CAP)
        self.assertAlmostEqual(f.growth_bytes_per_s * 3600 / GB, 6.0, delta=0.01)

    def test_hourly_temp_file_job_never_pages_after_first_hour(self):
        samples = hourly_temp_file_sawtooth()
        for end in range(13, len(samples) + 1):           # from 60 minutes of history
            alert = evaluate(samples[:end], CAP)
            self.assertFalse(alert and alert.severity == "page", (end, alert))

    def test_hourly_temp_file_job_is_silent_once_window_has_history(self):
        samples = hourly_temp_file_sawtooth()
        for end in range(4 * 12 + 1, len(samples) + 1):   # from 4 hours of history
            self.assertIsNone(evaluate(samples[:end], CAP), end)

    def test_shrinking_disk_no_alert(self):
        self.assertIsNone(evaluate(series(0.8, -1.0, seed=7), CAP))

    def test_hard_limit_pages_even_without_trend(self):
        self.assertEqual(evaluate(series(0.985, 0.0), CAP).severity, "page")

    def test_too_few_samples(self):
        f = forecast(series(0.5, 5.0, hours=0.25), CAP)       # 4 samples
        self.assertIsNone(f.seconds_to_full)

    def test_unsorted_input(self):
        s = series(0.50, 5.0, seed=8)
        shuffled = s[:]
        random.Random(1).shuffle(shuffled)
        self.assertAlmostEqual(forecast(shuffled, CAP).seconds_to_full,
                               forecast(s, CAP).seconds_to_full)

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            forecast([], CAP)
        with self.assertRaises(ValueError):
            forecast(series(0.5, 1.0), 0)


if __name__ == "__main__":
    unittest.main()

import unittest

from solution import (INCIDENT_START, MIN_FAILED_REQUESTS, MIN_REQUESTS, FakeClock,
                      SlidingWindowCounter, TrafficMinute, WindowCounts, example_day,
                      naive_count_alert, naive_ratio_alert, replay, volume_aware_alert)

LOW_COUNT_THRESHOLD = 50     # catches the incident, but noisy at peak
HIGH_COUNT_THRESHOLD = 300   # quiet at peak, but blind to the incident
INCIDENT_PAGE_MINUTE = INCIDENT_START + 2


def clock_time(hour: int, minute: int) -> int:
    """Minute of the day for hh:mm."""
    return hour * 60 + minute


def low_count_alert(counts: WindowCounts) -> bool:
    """Raw count alert tuned low enough to catch the evening incident."""
    return naive_count_alert(counts, LOW_COUNT_THRESHOLD)


def high_count_alert(counts: WindowCounts) -> bool:
    """Raw count alert tuned high enough to stay quiet at peak."""
    return naive_count_alert(counts, HIGH_COUNT_THRESHOLD)


def firing_minutes(traffic: list[TrafficMinute], alert) -> list[int]:
    """Every minute at which the alert condition is true."""
    clock = FakeClock()
    counter = SlidingWindowCounter(clock)
    firing = []
    for minute, sample in enumerate(traffic):
        clock.now = minute * 60
        counter.record(sample.requests, sample.failed)
        if alert(counter.counts()):
            firing.append(minute)
    return firing


def steady_traffic(minutes: int, requests: int, failed: int) -> list[TrafficMinute]:
    """The same traffic every minute."""
    return [TrafficMinute(requests, failed) for _ in range(minutes)]


class ReproduceProblem(unittest.TestCase):
    def test_ratio_alert_pages_at_3am_for_one_failed_request_out_of_two(self):
        pages = replay(example_day(), naive_ratio_alert)
        self.assertEqual(pages[0], clock_time(3, 5))

    def test_one_failure_in_two_requests_looks_like_fifty_percent_errors(self):
        self.assertEqual(WindowCounts(requests=2, failed=1).error_ratio(), 0.5)

    def test_low_count_alert_fires_all_day_from_background_errors_at_peak(self):
        healthy = [m for m in firing_minutes(example_day(), low_count_alert)
                   if m < INCIDENT_START]
        self.assertEqual(len(healthy), 545)
        self.assertEqual(healthy[0], clock_time(9, 2))

    def test_high_count_alert_misses_the_evening_incident(self):
        self.assertEqual(replay(example_day(), high_count_alert), [])


class FixVolumeAwareAlert(unittest.TestCase):
    def test_pages_exactly_once_on_the_example_day_for_the_real_incident(self):
        self.assertEqual(replay(example_day(), volume_aware_alert), [INCIDENT_PAGE_MINUTE])

    def test_one_failure_in_two_requests_does_not_page(self):
        self.assertFalse(volume_aware_alert(WindowCounts(requests=2, failed=1)))

    def test_peak_background_errors_do_not_page(self):
        self.assertFalse(volume_aware_alert(WindowCounts(requests=120_000, failed=240)))

    def test_high_ratio_with_enough_volume_pages(self):
        self.assertTrue(volume_aware_alert(WindowCounts(requests=1_000, failed=60)))

    def test_ratio_exactly_at_threshold_does_not_page(self):
        self.assertFalse(volume_aware_alert(WindowCounts(requests=1_000, failed=50)))

    def test_fewer_failures_than_the_absolute_floor_never_page(self):
        counts = WindowCounts(requests=MIN_REQUESTS, failed=MIN_FAILED_REQUESTS - 1)
        self.assertFalse(volume_aware_alert(counts))

    def test_low_traffic_total_outage_still_pages(self):
        self.assertTrue(volume_aware_alert(WindowCounts(requests=12, failed=12)))

    def test_low_traffic_with_minority_failing_does_not_page(self):
        self.assertFalse(volume_aware_alert(WindowCounts(requests=150, failed=20)))

    def test_night_outage_pages_once_ten_requests_have_failed(self):
        pages = replay(steady_traffic(30, requests=1, failed=1), volume_aware_alert)
        self.assertEqual(pages, [MIN_FAILED_REQUESTS - 1])

    def test_empty_window_does_not_page(self):
        self.assertFalse(volume_aware_alert(WindowCounts(requests=0, failed=0)))


class SlidingWindow(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.counter = SlidingWindowCounter(self.clock, window_minutes=10)

    def test_counts_cover_the_last_ten_minutes(self):
        for minute in range(15):
            self.clock.now = minute * 60
            self.counter.record(10, 1)
        self.assertEqual(self.counter.counts(), WindowCounts(requests=100, failed=10))

    def test_samples_in_the_same_minute_are_added_together(self):
        self.clock.now = 30
        self.counter.record(5, 1)
        self.clock.now = 59
        self.counter.record(5, 2)
        self.assertEqual(self.counter.counts(), WindowCounts(requests=10, failed=3))

    def test_old_failures_expire_even_without_new_traffic(self):
        self.counter.record(2, 1)
        self.clock.now = 10 * 60
        self.assertEqual(self.counter.counts(), WindowCounts(requests=0, failed=0))

    def test_expired_buckets_are_deleted(self):
        for minute in range(100):
            self.clock.now = minute * 60
            self.counter.record(1, 0)
        self.assertEqual(len(self.counter.buckets), 10)

    def test_more_failures_than_requests_is_rejected(self):
        with self.assertRaises(ValueError):
            self.counter.record(1, 2)


if __name__ == "__main__":
    unittest.main()

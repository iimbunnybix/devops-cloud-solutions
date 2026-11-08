"""Error-rate alerts that ignore a single failure at 3 a.m. but still catch real incidents.

The checkout API's alert paged when more than 5% of requests failed. At night
only a handful of requests arrive, so ONE failed request out of two is "50%
errors" and wakes on-call. Switching to a raw error count does not help: a
count low enough to catch quiet-hour incidents fires all day at peak from
normal background errors, and a count high enough to stay quiet at peak
misses a real outage in the evening.

The fix keeps the ratio but only trusts it once the window holds enough
requests (MIN_REQUESTS) and enough failures (MIN_FAILED_REQUESTS, an
absolute floor). A separate low-traffic rule still pages when most of the
few requests are failing. Counts come from a sliding window of per-minute
buckets driven by an injectable clock, so tests replay a whole day.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

SECONDS_PER_MINUTE = 60
WINDOW_MINUTES = 10
ERROR_RATIO_THRESHOLD = 0.05        # page when more than 5% of requests fail
# Below this many requests per window the ratio is mostly noise.
MIN_REQUESTS = 200
# Never page for fewer failed requests than this, whatever the ratio.
MIN_FAILED_REQUESTS = 10
# With little traffic, page only if most requests are failing.
LOW_TRAFFIC_ERROR_RATIO = 0.5


@dataclass
class WindowCounts:
    """Requests and failures seen in the last WINDOW_MINUTES."""
    requests: int
    failed: int

    def error_ratio(self) -> float:
        """Fraction of requests that failed (0.0 for an empty window)."""
        if self.requests == 0:
            return 0.0
        return self.failed / self.requests


class SlidingWindowCounter:
    """Counts requests and failures in per-minute buckets over a sliding window."""

    def __init__(self, clock: Callable[[], float], window_minutes: int = WINDOW_MINUTES) -> None:
        """``clock`` returns the current time in seconds (fake in tests)."""
        self.clock = clock
        self.window_minutes = window_minutes
        self.buckets: dict[int, list[int]] = {}   # minute -> [requests, failed]

    def _current_minute(self) -> int:
        """Index of the minute bucket the clock is in now."""
        return int(self.clock() // SECONDS_PER_MINUTE)

    def record(self, requests: int, failed: int) -> None:
        """Add ``requests`` (of which ``failed`` failed) to the current minute."""
        if requests < 0 or failed < 0 or failed > requests:
            raise ValueError("need 0 <= failed <= requests")
        bucket = self.buckets.setdefault(self._current_minute(), [0, 0])
        bucket[0] += requests
        bucket[1] += failed
        self._drop_expired_buckets()

    def _drop_expired_buckets(self) -> None:
        """Forget minutes that have left the window, so memory stays bounded."""
        oldest_kept = self._current_minute() - self.window_minutes + 1
        for minute in list(self.buckets):
            if minute < oldest_kept:
                del self.buckets[minute]

    def counts(self) -> WindowCounts:
        """Totals over the last ``window_minutes`` minutes, including this one."""
        oldest_kept = self._current_minute() - self.window_minutes + 1
        requests = 0
        failed = 0
        for minute, (bucket_requests, bucket_failed) in self.buckets.items():
            if minute >= oldest_kept:
                requests += bucket_requests
                failed += bucket_failed
        return WindowCounts(requests, failed)


def naive_ratio_alert(counts: WindowCounts) -> bool:
    """Original alert: page when the error ratio exceeds 5%, however few requests."""
    return counts.error_ratio() > ERROR_RATIO_THRESHOLD


def naive_count_alert(counts: WindowCounts, max_failed: int) -> bool:
    """Original 'fix': page when more than ``max_failed`` requests failed."""
    return counts.failed > max_failed


def volume_aware_alert(counts: WindowCounts) -> bool:
    """Fixed alert: a ratio rule that only trusts windows with enough data."""
    # Absolute floor: a few failures are never worth waking someone up for.
    if counts.failed < MIN_FAILED_REQUESTS:
        return False
    if counts.requests >= MIN_REQUESTS:
        return counts.error_ratio() > ERROR_RATIO_THRESHOLD
    # Quiet hours: too few requests for a 5% ratio to mean anything, but a
    # service failing most of them is still down.
    return counts.error_ratio() >= LOW_TRAFFIC_ERROR_RATIO


@dataclass
class TrafficMinute:
    """Requests and failures that arrived in one minute of the replay."""
    requests: int
    failed: int


class FakeClock:
    """A clock the replay moves forward by hand."""

    def __init__(self) -> None:
        """Start at time zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Current fake time in seconds."""
        return self.now


Alert = Callable[[WindowCounts], bool]


def replay(traffic: list[TrafficMinute], alert: Alert) -> list[int]:
    """Feed traffic minute by minute; return the minutes at which a page starts.

    A page starts when the alert goes from not firing to firing, which is
    what on-call actually experiences as one phone call.
    """
    clock = FakeClock()
    counter = SlidingWindowCounter(clock)
    pages: list[int] = []
    was_firing = False
    for minute, sample in enumerate(traffic):
        clock.now = minute * SECONDS_PER_MINUTE
        counter.record(sample.requests, sample.failed)
        firing = alert(counter.counts())
        if firing and not was_firing:
            pages.append(minute)
        was_firing = firing
    return pages


# Requests per minute for each hour of the example day (0 = sparse night).
HOURLY_REQUESTS_PER_MINUTE = [0, 0, 0, 0, 0, 0, 500, 500, 2_000, 4_000, 4_000, 4_000,
                              12_000, 12_000, 4_000, 4_000, 4_000, 4_000, 2_000, 2_000,
                              2_000, 2_000, 100, 100]
NIGHT_REQUEST_EVERY_MINUTES = 5          # at night one request every 5 minutes
BACKGROUND_FAILURES_PER_THOUSAND = 2     # 0.2% of requests fail on a healthy day
NIGHT_FAILURE_MINUTE = 3 * 60 + 5        # the lone failed request at 03:05
INCIDENT_START, INCIDENT_END = 22 * 60 + 15, 22 * 60 + 45
INCIDENT_FAILURES_PER_THOUSAND = 200     # 20% of requests fail during the incident


def requests_in_minute(minute: int) -> int:
    """Traffic for one minute of the example day."""
    per_minute = HOURLY_REQUESTS_PER_MINUTE[minute // 60]
    if per_minute > 0:
        return per_minute
    return 1 if minute % NIGHT_REQUEST_EVERY_MINUTES == 0 else 0


def example_day() -> list[TrafficMinute]:
    """A 24-hour day: sparse nights, a lunchtime peak and a late-evening incident."""
    day: list[TrafficMinute] = []
    for minute in range(24 * 60):
        requests = requests_in_minute(minute)
        rate = BACKGROUND_FAILURES_PER_THOUSAND
        if INCIDENT_START <= minute < INCIDENT_END:
            rate = INCIDENT_FAILURES_PER_THOUSAND
        failed = requests * rate // 1000
        if minute == NIGHT_FAILURE_MINUTE:
            failed = requests
        day.append(TrafficMinute(requests, failed))
    return day

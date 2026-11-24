"""Measure a latency SLO on a histogram bucket that matches the SLO threshold.

The API's SLO was "99% of requests within 300 ms", but its latency histogram
had buckets at 250 ms and 500 ms only. The SLO query counted the 500 ms
bucket as "good" because it was the nearest one, so a release that pushed
about 4% of requests to 300-430 ms left the SLO dashboard at 99.6% and burned no
error budget, while users saw the slowdown.

Fix: add a bucket boundary exactly at the SLO threshold, refuse to compute
the SLO when no such boundary exists (instead of rounding or interpolating),
and read the good-request count from that exact bucket.
"""
from __future__ import annotations

import bisect
import random

SLO_THRESHOLD_SECONDS = 0.3
SLO_TARGET = 0.99
# The buckets the service shipped with (a common default-like layout).
ORIGINAL_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
# Same layout plus a boundary exactly at the SLO threshold.
SLO_ALIGNED_BUCKETS = (0.05, 0.1, 0.25, SLO_THRESHOLD_SECONDS, 0.5, 1.0, 2.5, 5.0)


class Histogram:
    """A Prometheus-style histogram: cumulative counts per upper bound ("le")."""

    def __init__(self, upper_bounds: tuple[float, ...]) -> None:
        if list(upper_bounds) != sorted(set(upper_bounds)):
            raise ValueError("bucket bounds must be strictly increasing")
        self.upper_bounds = upper_bounds
        self.bucket_counts = [0] * (len(upper_bounds) + 1)   # last one is +Inf
        self.total = 0

    def observe(self, seconds: float) -> None:
        """Count one request in the first bucket whose bound is >= its latency."""
        # bisect_left keeps a value equal to a bound in that bound's bucket ("le" = <=).
        self.bucket_counts[bisect.bisect_left(self.upper_bounds, seconds)] += 1
        self.total += 1

    def count_at_or_below(self, bound: float) -> int:
        """Cumulative count for ``le=bound``; ``bound`` must be one of the bucket bounds."""
        index = self.upper_bounds.index(bound)
        return sum(self.bucket_counts[: index + 1])


class SloBucketMissing(Exception):
    """The histogram cannot tell whether requests met the SLO threshold."""


# --- the original SLO query ------------------------------------------------------
def naive_good_fraction(histogram: Histogram, threshold: float = SLO_THRESHOLD_SECONDS) -> float:
    """Original query: use the first bucket at or above the threshold (here 0.5 s).

    WHY this is the bug: every request between 300 ms and 500 ms is counted
    as meeting a 300 ms SLO.
    """
    nearest_index = bisect.bisect_left(histogram.upper_bounds, threshold)
    nearest_bound = histogram.upper_bounds[nearest_index]
    return histogram.count_at_or_below(nearest_bound) / histogram.total


def interpolated_good_fraction(histogram: Histogram, threshold: float = SLO_THRESHOLD_SECONDS) -> float:
    """What ``histogram_quantile``-style linear interpolation would estimate.

    It assumes requests are spread evenly inside the bucket, which is exactly
    what a regression breaks: the slow requests bunch together.
    """
    upper_index = bisect.bisect_left(histogram.upper_bounds, threshold)
    upper = histogram.upper_bounds[upper_index]
    lower = histogram.upper_bounds[upper_index - 1] if upper_index > 0 else 0.0
    below_lower = histogram.count_at_or_below(lower) if upper_index > 0 else 0
    in_bucket = histogram.bucket_counts[upper_index]
    share_of_bucket = (threshold - lower) / (upper - lower)
    return (below_lower + in_bucket * share_of_bucket) / histogram.total


# --- the fix ---------------------------------------------------------------------------
def require_slo_bucket(upper_bounds: tuple[float, ...], threshold: float) -> None:
    """Fail loudly at configuration time if no bucket boundary equals the SLO threshold."""
    if threshold not in upper_bounds:
        raise SloBucketMissing(
            f"no histogram bucket at le={threshold}; add one before defining this SLO")


def good_fraction(histogram: Histogram, threshold: float = SLO_THRESHOLD_SECONDS) -> float:
    """Exact share of requests at or below the threshold, read from its own bucket."""
    require_slo_bucket(histogram.upper_bounds, threshold)
    if histogram.total == 0:
        # WHY: no traffic means no evidence of a breach; do not divide by zero.
        return 1.0
    return histogram.count_at_or_below(threshold) / histogram.total


def error_budget_used(good: float, target: float = SLO_TARGET) -> float:
    """Share of the error budget consumed: 1.0 means the whole budget is gone."""
    return (1.0 - good) / (1.0 - target)


# --- simulated traffic -----------------------------------------------------------------
def simulated_latencies(request_count: int, slowed_share: float, added_seconds: float,
                        seed: int) -> list[float]:
    """Per-request latencies: mostly 20-280 ms, a 0.4% slow tail, plus a regression.

    ``slowed_share`` of requests get ``added_seconds`` extra, like a new
    synchronous call on a cache miss.
    """
    rng = random.Random(seed)
    latencies = []
    for _ in range(request_count):
        if rng.random() < 0.004:
            latency = rng.uniform(0.6, 2.0)
        else:
            latency = rng.uniform(0.02, 0.28)
        if rng.random() < slowed_share:
            latency += added_seconds
        latencies.append(latency)
    return latencies


def histogram_of(latencies: list[float], upper_bounds: tuple[float, ...]) -> Histogram:
    """Record every latency into a new histogram with the given buckets."""
    histogram = Histogram(upper_bounds)
    for latency in latencies:
        histogram.observe(latency)
    return histogram


def true_good_fraction(latencies: list[float], threshold: float = SLO_THRESHOLD_SECONDS) -> float:
    """Ground truth from raw latencies, for comparison only (a real system has no raw data)."""
    good = 0
    for latency in latencies:
        if latency <= threshold:
            good += 1
    return good / len(latencies)

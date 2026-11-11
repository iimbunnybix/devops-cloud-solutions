"""Measure a host's clock offset correctly and alert on real skew.

The old skew check compared the host's send time with the time server's
receive time. That difference also contains the network delay, so it raised
false alarms on every slow request and was muted, and when a VM's clock really
drifted minutes behind, nobody noticed until login tokens were rejected.
This module uses the NTP formula, which cancels the delay out when it is
symmetric, keeps only the lowest round-trip samples (their error bound is the
smallest), and alerts when the offset passes a threshold.
"""
from __future__ import annotations

from dataclasses import dataclass

SKEW_ALERT_THRESHOLD_S = 0.5     # most auth/token checks allow ~1 s or more
BEST_SAMPLES_TO_KEEP = 3         # NTP-style: trust only the fastest exchanges
MICROSECONDS_PER_SECOND = 1_000_000


@dataclass(frozen=True)
class TimeSample:
    """One request/response exchange with a trusted time server.

    t0: client clock when the request left
    t1: server clock when the request arrived
    t2: server clock when the response left
    t3: client clock when the response arrived
    """
    t0: float
    t1: float
    t2: float
    t3: float


@dataclass(frozen=True)
class SkewReport:
    """Estimated offset (server minus client) with its worst-case error."""
    offset_s: float
    error_bound_s: float
    alert: bool


def naive_offset(sample: TimeSample) -> float:
    """Original check: server receive time minus client send time.

    This is the one-way network delay plus the real offset, so a perfectly
    synced host on a slow network looks skewed.
    """
    return sample.t1 - sample.t0


def ntp_offset(sample: TimeSample) -> float:
    """NTP offset: average the two one-way differences so the delay cancels."""
    outbound = sample.t1 - sample.t0
    inbound = sample.t2 - sample.t3
    return (outbound + inbound) / 2


def round_trip_delay(sample: TimeSample) -> float:
    """Time spent on the network, excluding the server's processing time."""
    return (sample.t3 - sample.t0) - (sample.t2 - sample.t1)


def best_samples(samples: list[TimeSample], keep: int) -> list[TimeSample]:
    """Return the ``keep`` samples with the lowest round-trip delay.

    If the delay was asymmetric the offset is wrong by at most half the round
    trip, so the fastest exchanges give the tightest estimate.
    """
    ordered = sorted(samples, key=round_trip_delay)
    return ordered[:keep]


def median(values: list[float]) -> float:
    """Middle value; average of the two middle values for an even count."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def worst_case_error(samples: list[TimeSample]) -> float:
    """Largest half round trip among the samples: the error bound for any of their offsets."""
    largest_delay = 0.0
    for sample in samples:
        largest_delay = max(largest_delay, round_trip_delay(sample))
    return largest_delay / 2


def estimate_skew(samples: list[TimeSample],
                  threshold_s: float = SKEW_ALERT_THRESHOLD_S,
                  keep: int = BEST_SAMPLES_TO_KEEP) -> SkewReport:
    """Estimate the offset from the fastest samples and decide whether to alert."""
    if not samples:
        raise ValueError("need at least one time sample")
    fastest = best_samples(samples, keep)
    offsets = []
    for sample in fastest:
        offsets.append(ntp_offset(sample))
    offset = median(offsets)
    # The median may come from any of the kept samples, so the bound must cover
    # the slowest of them; the fastest sample's bound alone would be too tight.
    error_bound = worst_case_error(fastest)
    # Alert only if the skew is beyond the threshold even in the best case,
    # so network noise alone can never page anyone.
    alert = abs(offset) - error_bound > threshold_s
    return SkewReport(offset_s=offset, error_bound_s=error_bound, alert=alert)


def drift_rate_ppm(checks: list[tuple[float, float]]) -> float:
    """Least-squares slope of offset over time, in parts per million.

    ``checks`` holds (check time, offset) pairs. A steady slope means the
    clock runs fast or slow and will cross the threshold again after a fix.
    """
    if len(checks) < 2:
        raise ValueError("need at least two checks to measure drift")
    times = [check_time for check_time, _ in checks]
    offsets = [offset for _, offset in checks]
    mean_time = sum(times) / len(times)
    mean_offset = sum(offsets) / len(offsets)
    numerator = 0.0
    denominator = 0.0
    for check_time, offset in checks:
        numerator += (check_time - mean_time) * (offset - mean_offset)
        denominator += (check_time - mean_time) ** 2
    if denominator == 0:
        raise ValueError("checks must be taken at different times")
    return numerator / denominator * MICROSECONDS_PER_SECOND

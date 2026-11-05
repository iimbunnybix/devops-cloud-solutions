"""Compute rates from cumulative counters without breaking on process restarts.

Services export counters such as ``http_requests_total`` that only ever go up
until the process restarts, when they start again from zero. The original
dashboard computed "requests in the last 5 minutes" as ``last - first``, so a
restart inside the window produced a huge negative number: the request-rate
alert fired "traffic dropped to zero" and the error-ratio alert, dividing one
negative number by another, stayed silent while the restarted pod was failing.
This module sums the increase sample by sample and treats any decrease as a
reset (the counter restarted from 0), which is how Prometheus' ``increase()``
and ``rate()`` work.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# Page when more than 5% of requests in the window failed.
ERROR_RATIO_PAGE_THRESHOLD = 0.05
# Need at least two samples to see any change in a counter.
MIN_SAMPLES = 2


@dataclass(frozen=True)
class CounterSample:
    """One scrape of a cumulative counter."""
    timestamp_s: float
    value: float


def _sorted_samples(samples: Sequence[CounterSample]) -> list[CounterSample]:
    """Return samples oldest first; scrapes can arrive out of order after retries."""
    return sorted(samples, key=lambda sample: sample.timestamp_s)


def naive_increase(samples: Sequence[CounterSample]) -> float | None:
    """The original query: last value minus first value. Wrong across a restart."""
    if len(samples) < MIN_SAMPLES:
        return None
    ordered = _sorted_samples(samples)
    return ordered[-1].value - ordered[0].value


def reset_aware_increase(samples: Sequence[CounterSample]) -> float | None:
    """Total increase over the samples, counting through counter resets.

    A counter never goes down while the process lives, so a lower value than
    the previous sample means the process restarted and counted up from 0 to
    the new value. In that case the whole new value is the increase.
    """
    if len(samples) < MIN_SAMPLES:
        return None
    ordered = _sorted_samples(samples)
    total = 0.0
    for previous, current in zip(ordered, ordered[1:]):
        if current.value >= previous.value:
            total += current.value - previous.value
        else:
            # Reset: everything counted since the restart is new.
            total += current.value
    return total


def per_second_rate(samples: Sequence[CounterSample]) -> float | None:
    """Reset-aware increase divided by the time the samples cover."""
    increase = reset_aware_increase(samples)
    if increase is None:
        return None
    ordered = _sorted_samples(samples)
    span_s = ordered[-1].timestamp_s - ordered[0].timestamp_s
    if span_s <= 0:
        return None
    return increase / span_s


def error_ratio(request_samples: Sequence[CounterSample],
                error_samples: Sequence[CounterSample],
                use_reset_aware: bool = True) -> float | None:
    """Fraction of requests in the window that failed, or None with no traffic."""
    increase_function = reset_aware_increase if use_reset_aware else naive_increase
    requests = increase_function(request_samples)
    errors = increase_function(error_samples)
    if requests is None or errors is None or requests == 0:
        return None
    return errors / requests


def should_page_on_errors(request_samples: Sequence[CounterSample],
                          error_samples: Sequence[CounterSample],
                          use_reset_aware: bool = True) -> bool:
    """True when the error ratio in the window is above the page threshold."""
    ratio = error_ratio(request_samples, error_samples, use_reset_aware)
    if ratio is None:
        return False
    return ratio > ERROR_RATIO_PAGE_THRESHOLD

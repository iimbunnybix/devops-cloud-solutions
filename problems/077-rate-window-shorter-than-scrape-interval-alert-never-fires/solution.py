"""Size rate() windows from the scrape interval so error alerts can fire.

The checkout error alert was ``rate(errors_total[1m]) > 5``. The target was
scraped once a minute, so any 1-minute window held at most one sample, and a
rate needs at least two. The expression returned no data at every
evaluation, the rule never fired, and a 20-minute outage paged nobody.

Fix: compute the window from the scrape interval (at least 4x, so a window
still has two or more samples even after a failed scrape) and lint alert
rules for windows that are too short.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_SAMPLES_FOR_RATE = 2
SCRAPES_PER_WINDOW = 4          # guidance: rate window >= 4x scrape interval


@dataclass(frozen=True)
class Sample:
    timestamp: float            # seconds
    value: float                # cumulative counter value


def samples_in_window(samples: list[Sample], now: float, window: float) -> list[Sample]:
    """Samples with ``now - window < timestamp <= now``, like a PromQL range selector."""
    selected = []
    for sample in samples:
        if now - window < sample.timestamp <= now:
            selected.append(sample)
    return selected


def per_second_rate(samples: list[Sample], now: float, window: float) -> float | None:
    """Increase per second between the first and last sample in the window.

    Returns None ("no data") with fewer than two samples: one point has no
    slope. Counter resets are ignored here to keep the example short.
    """
    selected = samples_in_window(samples, now, window)
    if len(selected) < MIN_SAMPLES_FOR_RATE:
        return None
    first, last = selected[0], selected[-1]
    return (last.value - first.value) / (last.timestamp - first.timestamp)


def alert_firing_times(samples: list[Sample], window: float, threshold: float,
                       evaluation_times: list[float]) -> list[float]:
    """Evaluation times at which ``rate(counter[window]) > threshold``."""
    firing = []
    for now in evaluation_times:
        rate = per_second_rate(samples, now, window)
        # WHY: "no data" is not "above threshold", so an empty result never fires.
        if rate is not None and rate > threshold:
            firing.append(now)
    return firing


def no_data_share(samples: list[Sample], window: float, evaluation_times: list[float]) -> float:
    """Fraction of evaluations where the rate had no data."""
    empty = 0
    for now in evaluation_times:
        if per_second_rate(samples, now, window) is None:
            empty += 1
    return empty / len(evaluation_times)


def safe_rate_window(scrape_interval: float, requested_window: float = 0.0) -> float:
    """The requested window, raised to at least SCRAPES_PER_WINDOW scrape intervals."""
    return max(requested_window, SCRAPES_PER_WINDOW * scrape_interval)


def lint_rate_windows(rules: dict[str, float], scrape_interval: float) -> list[str]:
    """Error messages for rules whose rate window is shorter than the safe minimum."""
    minimum = safe_rate_window(scrape_interval)
    problems = []
    for rule_name, window in sorted(rules.items()):
        if window < minimum:
            problems.append(f"{rule_name}: window {window:g}s < {minimum:g}s "
                            f"({SCRAPES_PER_WINDOW} x {scrape_interval:g}s scrape interval)")
    return problems


def scrape_counter(start: float, end: float, scrape_interval: float,
                   errors_per_second: float, missed_scrapes: set[float] | None = None) -> list[Sample]:
    """Simulate scraping an error counter that grows at a steady rate."""
    missed = missed_scrapes or set()
    samples = []
    timestamp = start
    while timestamp <= end:
        if timestamp not in missed:
            samples.append(Sample(timestamp, errors_per_second * (timestamp - start)))
        timestamp += scrape_interval
    return samples

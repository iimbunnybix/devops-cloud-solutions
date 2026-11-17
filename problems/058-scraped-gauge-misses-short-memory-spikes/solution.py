"""Alert on the peak memory between scrapes, not on the value at scrape time.

A worker's memory gauge was scraped every 60 seconds. Batch jobs pushed memory
to the container limit for a few seconds at a time, and the kernel OOM-killed
the worker, but every scrape happened to land between spikes, so the memory
alert never fired and dashboards showed a flat 45%. This module keeps a
high-water mark inside the process that is reset each time it is scraped, so
the exported value is the peak of the whole interval and short spikes are seen.
"""
from __future__ import annotations

from dataclasses import dataclass

SCRAPE_INTERVAL_SECONDS = 60
ALERT_THRESHOLD_PERCENT = 90.0


class InstantGauge:
    """Original metric: the scrape reads whatever the value is right now."""

    def __init__(self) -> None:
        self.current = 0.0

    def set(self, value: float) -> None:
        self.current = value

    def scrape(self) -> float:
        return self.current


class PeakSinceLastScrapeGauge:
    """Remembers the highest value seen since the previous scrape.

    The application updates it on every sample (cheap: one comparison), so
    nothing between two scrapes is lost however short it was.
    """

    def __init__(self) -> None:
        self.current = 0.0
        self.peak = 0.0

    def set(self, value: float) -> None:
        self.current = value
        if value > self.peak:
            self.peak = value

    def scrape(self) -> float:
        """Return the interval's peak and start a new interval at the current value."""
        peak = self.peak
        # Start the next interval from the current value, not 0, so a value
        # that stays high is still reported as high on the next scrape.
        self.peak = self.current
        return peak


@dataclass(frozen=True)
class Spike:
    start_second: int
    duration_seconds: int
    percent: float


def memory_percent_at(second: int, baseline: float, spikes: list[Spike]) -> float:
    """Memory usage of the simulated worker at a given second."""
    for spike in spikes:
        if spike.start_second <= second < spike.start_second + spike.duration_seconds:
            return spike.percent
    return baseline


def run_and_scrape(gauge: InstantGauge | PeakSinceLastScrapeGauge, total_seconds: int,
                   baseline: float, spikes: list[Spike],
                   scrape_interval: int = SCRAPE_INTERVAL_SECONDS) -> list[float]:
    """Sample memory every second into the gauge and scrape it on the interval."""
    if scrape_interval < 1:
        raise ValueError("scrape interval must be at least one second")
    scraped = []
    for second in range(total_seconds):
        gauge.set(memory_percent_at(second, baseline, spikes))
        # Scrape at the end of each interval, as Prometheus would.
        if (second + 1) % scrape_interval == 0:
            scraped.append(gauge.scrape())
    return scraped


def alert_fires(scraped_values: list[float], threshold: float = ALERT_THRESHOLD_PERCENT) -> bool:
    """True if any scraped value reaches the threshold."""
    for value in scraped_values:
        if value >= threshold:
            return True
    return False

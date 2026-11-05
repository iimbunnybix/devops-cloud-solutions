"""Make an error-rate alert page when its metrics disappear, not only when they are bad.

The original bug: the checkout error-rate alert computed ``errors / requests``
over the scrapes it had in the last 5 minutes and returned OK when it had
none. A deploy moved the service's metrics endpoint, so every scrape failed
from that moment on. The same deploy broke checkout, but the alert saw "no
data", evaluated that as healthy, and never paged.

Fix: the evaluator has an explicit NO_DATA state. When the most recent
scrapes have been missing for ``ABSENT_AFTER_MINUTES`` in a row, it returns
NO_DATA, which pages like FIRING. A single missed scrape is still tolerated,
and the error ratio is computed only over scrapes that actually arrived.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Optional

WINDOW_MINUTES = 5                 # error ratio is computed over the last 5 scrapes (1 per minute)
ERROR_RATIO_THRESHOLD = 0.02       # page when more than 2% of checkouts fail
ABSENT_AFTER_MINUTES = 5           # this many missing scrapes in a row means "no data"

OK, FIRING, NO_DATA = "OK", "FIRING", "NO_DATA"
PAGING_STATES = {FIRING, NO_DATA}


@dataclass(frozen=True)
class Scrape:
    """Request and error counts the service reported for one minute."""

    requests: int
    errors: int


# A missing scrape (target down, endpoint moved, exporter crashed) is None.
Series = list[Optional[Scrape]]
Evaluator = Callable[[Series, int], str]


def window_at(series: Series, minute: int) -> Series:
    """The scrapes that fall in the evaluation window ending at ``minute`` (inclusive)."""
    start = max(0, minute - WINDOW_MINUTES + 1)
    return series[start:minute + 1]


def error_ratio(scrapes: list[Scrape]) -> float:
    """Errors divided by requests over the given scrapes; 0.0 when there was no traffic."""
    total_requests = 0
    total_errors = 0
    for scrape in scrapes:
        total_requests += scrape.requests
        total_errors += scrape.errors
    if total_requests == 0:
        return 0.0
    return total_errors / total_requests


def naive_evaluate(series: Series, minute: int) -> str:
    """The original rule: ratio over whatever arrived, and no data counts as OK."""
    present = [scrape for scrape in window_at(series, minute) if scrape is not None]
    if not present:
        return OK
    if error_ratio(present) > ERROR_RATIO_THRESHOLD:
        return FIRING
    return OK


def consecutive_missing(series: Series, minute: int) -> int:
    """How many scrapes in a row, ending at ``minute``, are missing."""
    count = 0
    index = minute
    while index >= 0 and series[index] is None:
        count += 1
        index -= 1
    return count


def evaluate(series: Series, minute: int) -> str:
    """Return OK, FIRING or NO_DATA for the window ending at ``minute``.

    WHY the order: absence is checked first, because an empty window can
    never prove the service is healthy.
    """
    if consecutive_missing(series, minute) >= ABSENT_AFTER_MINUTES:
        return NO_DATA
    present = [scrape for scrape in window_at(series, minute) if scrape is not None]
    if not present:
        # Fewer than ABSENT_AFTER_MINUTES gaps (only possible right after start):
        # not enough evidence either way yet.
        return OK
    if error_ratio(present) > ERROR_RATIO_THRESHOLD:
        return FIRING
    return OK


def first_page_minute(series: Series, evaluator: Evaluator) -> int | None:
    """The first minute at which ``evaluator`` returns a paging state, or None."""
    for minute in range(len(series)):
        if evaluator(series, minute) in PAGING_STATES:
            return minute
    return None


def checkout_traffic(minutes: int, requests_per_minute: int, error_ratio_value: float,
                     rng: random.Random) -> list[Scrape]:
    """Per-minute scrapes with each request failing independently at ``error_ratio_value``."""
    scrapes = []
    for _ in range(minutes):
        errors = 0
        for _ in range(requests_per_minute):
            if rng.random() < error_ratio_value:
                errors += 1
        scrapes.append(Scrape(requests_per_minute, errors))
    return scrapes


def broken_deploy_incident(seed: int = 7) -> tuple[Series, list[Scrape]]:
    """Two hours of checkout: a deploy at minute 60 breaks checkout AND the metrics endpoint.

    Returns the series the alert sees (scrapes missing after the deploy) and
    the true traffic (what the service actually did), for comparison.
    """
    rng = random.Random(seed)
    healthy = checkout_traffic(60, requests_per_minute=1_200, error_ratio_value=0.003, rng=rng)
    broken = checkout_traffic(60, requests_per_minute=1_200, error_ratio_value=0.30, rng=rng)
    truth = healthy + broken
    observed: Series = list(healthy) + [None] * len(broken)
    return observed, truth

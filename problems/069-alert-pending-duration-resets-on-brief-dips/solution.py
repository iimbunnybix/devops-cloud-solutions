"""Page on a sustained error rate even when it briefly dips below the threshold.

The checkout error-rate alert had a pending duration ("for: 10m"): it fired
only after 10 consecutive one-minute evaluations above 5%. During a partial
outage the error rate sat around 8% but fell under 5% for one minute every
few minutes, so the pending timer kept restarting from zero and the alert
never fired for two hours.

Fix: decide on the share of breaching evaluations in a sliding window (fire
when at least 80% of the last 10 minutes breached, resolve when at most 20%
did). One good minute no longer erases nine bad ones, and the gap between
the fire and resolve levels stops the alert flapping.
"""
from __future__ import annotations

from collections import deque

ERROR_RATE_THRESHOLD_PERCENT = 5.0
PENDING_MINUTES = 10          # the original rule: "for: 10m"
WINDOW_MINUTES = 10           # the fixed rule looks at the same 10 minutes...
FIRE_FRACTION = 0.8           # ...and fires when 8 of them breached
RESOLVE_FRACTION = 0.2        # ...and resolves when at most 2 of them breached


def breaches(error_rate_percent: float) -> bool:
    """True if one evaluation is above the alert threshold."""
    return error_rate_percent > ERROR_RATE_THRESHOLD_PERCENT


# --- the original rule ---------------------------------------------------------
def naive_pending_alert(error_rates: list[float], pending_minutes: int = PENDING_MINUTES) -> list[int]:
    """Minutes (0-based) at which a page is sent by a consecutive-breach rule.

    Like a Prometheus alert with ``for:``: any evaluation under the threshold
    throws away the pending time collected so far.
    """
    pages = []
    consecutive = 0
    firing = False
    for minute, rate in enumerate(error_rates):
        if breaches(rate):
            consecutive += 1
        else:
            # WHY this is the bug: one good minute resets the whole timer.
            consecutive = 0
            firing = False
        if consecutive >= pending_minutes and not firing:
            firing = True
            pages.append(minute)
    return pages


# --- the fix ---------------------------------------------------------------------
class WindowedAlert:
    """Fires on the share of breaching minutes in a sliding window, with hysteresis."""

    def __init__(self, window_minutes: int = WINDOW_MINUTES, fire_fraction: float = FIRE_FRACTION,
                 resolve_fraction: float = RESOLVE_FRACTION) -> None:
        if resolve_fraction >= fire_fraction:
            raise ValueError("resolve_fraction must be below fire_fraction, or the alert flaps")
        self.window: deque[bool] = deque(maxlen=window_minutes)
        self.fire_fraction = fire_fraction
        self.resolve_fraction = resolve_fraction
        self.firing = False

    def breach_fraction(self) -> float:
        """Share of evaluations in the window that were above the threshold."""
        if not self.window:
            return 0.0
        return sum(self.window) / len(self.window)

    def evaluate(self, error_rate_percent: float) -> bool:
        """Add one evaluation; return True if this evaluation sends a new page."""
        self.window.append(breaches(error_rate_percent))
        # WHY: wait for a full window so a restart cannot page on 2 minutes of data.
        if len(self.window) < self.window.maxlen:
            return False
        fraction = self.breach_fraction()
        if not self.firing and fraction >= self.fire_fraction:
            self.firing = True
            return True
        if self.firing and fraction <= self.resolve_fraction:
            self.firing = False
        return False


def windowed_alert_pages(error_rates: list[float]) -> list[int]:
    """Minutes (0-based) at which the windowed rule sends a page."""
    alert = WindowedAlert()
    pages = []
    for minute, rate in enumerate(error_rates):
        if alert.evaluate(rate):
            pages.append(minute)
    return pages


# --- the incident as data -------------------------------------------------------
def flapping_outage(healthy_minutes: int, outage_minutes: int, dip_every: int,
                    bad_rate: float, dip_rate: float, healthy_rate: float) -> list[float]:
    """Per-minute error rates: healthy, then an outage that dips once every ``dip_every`` minutes."""
    rates = [healthy_rate] * healthy_minutes
    for minute in range(outage_minutes):
        # WHY: retries and a lucky minute of cache hits made the rate dip on a rhythm.
        if minute % dip_every == dip_every - 1:
            rates.append(dip_rate)
        else:
            rates.append(bad_rate)
    rates += [healthy_rate] * healthy_minutes
    return rates

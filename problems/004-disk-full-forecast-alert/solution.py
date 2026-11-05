"""Predictive "disk will be full in N hours" alerting.

Static thresholds ("used > 90%") are wrong in both directions: a volume
sitting at 91% for months pages every night, while a volume filling at
5%/hour goes from 80% to 100% in four hours and the 90% page arrives
two hours before the outage. This module fits a least-squares line to the
recent usage samples and alerts on *time until full*.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# A post-drop segment shorter than this is too short to trust on its own: a
# job that writes temp files and then deletes them would restart the fit every
# cycle and only ever see the steep "write" half.
MIN_SPAN_AFTER_DROP_S = 2 * 3600


@dataclass(frozen=True)
class Sample:
    ts: float          # seconds
    used_bytes: float


@dataclass(frozen=True)
class Forecast:
    seconds_to_full: float | None   # None = not growing / no meaningful trend
    growth_bytes_per_s: float
    used_fraction: float
    samples_used: int


@dataclass(frozen=True)
class Alert:
    severity: str      # "page" | "ticket"
    message: str


def static_threshold_alert(samples: Sequence[Sample], capacity: float,
                           threshold: float = 0.9) -> bool:
    """The original rule: fire whenever usage is above 90%."""
    return samples[-1].used_bytes / capacity > threshold


def _since_last_drop(samples: Sequence[Sample], capacity: float,
                     drop_fraction: float) -> Sequence[Sample]:
    """Keep only samples after the last big drop (log rotation, cleanup job).

    Fitting across a drop would average two regimes and understate growth.
    """
    start = 0
    for i in range(1, len(samples)):
        if samples[i - 1].used_bytes - samples[i].used_bytes > drop_fraction * capacity:
            start = i
    return samples[start:]


def linear_fit(samples: Sequence[Sample]) -> tuple[float, float]:
    """Ordinary least squares: return (slope bytes/s, intercept at samples[0].ts)."""
    t0 = samples[0].ts
    xs = [s.ts - t0 for s in samples]
    ys = [s.used_bytes for s in samples]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return slope, my - slope * mx


def forecast(samples: Sequence[Sample], capacity: float, window_s: float = 6 * 3600,
             min_samples: int = 6, drop_fraction: float = 0.05) -> Forecast:
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if not samples:
        raise ValueError("no samples")
    samples = sorted(samples, key=lambda s: s.ts)
    now = samples[-1].ts
    recent = [s for s in samples if s.ts >= now - window_s]
    after_drop = list(_since_last_drop(recent, capacity, drop_fraction))
    # Only reset the trend when the new regime has lasted long enough to fit;
    # otherwise the whole window (e.g. a full sawtooth) gives the real trend.
    if after_drop[-1].ts - after_drop[0].ts >= MIN_SPAN_AFTER_DROP_S:
        recent = after_drop
    used = samples[-1].used_bytes / capacity
    if len(recent) < min_samples:
        return Forecast(None, 0.0, used, len(recent))
    slope, intercept = linear_fit(recent)
    if slope <= 0:
        return Forecast(None, slope, used, len(recent))
    # project from the fitted value at "now" to reduce sensitivity to one noisy sample
    fitted_now = intercept + slope * (now - recent[0].ts)
    remaining = max(0.0, capacity - max(fitted_now, samples[-1].used_bytes))
    return Forecast(remaining / slope, slope, used, len(recent))


def evaluate(samples: Sequence[Sample], capacity: float, page_within_s: float = 4 * 3600,
             ticket_within_s: float = 4 * 86400, hard_limit: float = 0.98) -> Alert | None:
    """Page if full within 4 h (or already ≥98%), ticket if full within 4 days."""
    f = forecast(samples, capacity)
    if f.used_fraction >= hard_limit:
        return Alert("page", f"disk {f.used_fraction:.1%} used")
    if f.seconds_to_full is None:
        return None
    hours = f.seconds_to_full / 3600
    if f.seconds_to_full <= page_within_s:
        return Alert("page", f"disk full in {hours:.1f}h at current growth")
    if f.seconds_to_full <= ticket_within_s:
        return Alert("ticket", f"disk full in {hours / 24:.1f}d at current growth")
    return None

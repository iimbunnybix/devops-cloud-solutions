"""Stop a maintenance-window silence from hiding unrelated outages.

The original bug: before planned database maintenance, an engineer silenced
alerts with the matcher ``env=prod, region=eu-west-1`` and no end time,
because that was the quickest way to stop the expected noise. The silence
matched every production alert in the region and never expired, so a
checkout outage during the window and a disk alert two days later were
never delivered to on-call.

Fix: silences are validated before they are created. Every silence must
name a specific scope (a ``service`` plus a ``cluster`` or ``instance``),
may not use catch-all labels alone, must have an end time, and may last at
most a few hours. Alerts suppressed by a silence are still recorded, so the
silence owner can review what it hid.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HOUR = 3600
MAX_SILENCE_SECONDS = 4 * HOUR          # longest planned window we allow without review
REQUIRED_LABEL = "service"
NARROWING_LABELS = ("cluster", "instance")   # at least one must pin the exact target
NEVER_SILENCED_LABEL = "silence_exempt"      # e.g. the "alerting pipeline is down" alert


class SilenceRejected(ValueError):
    """Raised when a silence is too broad or has no safe end time."""


@dataclass(frozen=True)
class Alert:
    name: str
    labels: dict[str, str]
    fired_at: float


@dataclass(frozen=True)
class Silence:
    matchers: dict[str, str]        # label -> exact value; all must match
    starts_at: float
    ends_at: float | None           # None means "until someone deletes it"
    created_by: str
    comment: str


@dataclass
class RoutingResult:
    delivered: list[Alert] = field(default_factory=list)
    silenced: list[tuple[Alert, Silence]] = field(default_factory=list)

    def delivered_names(self) -> list[str]:
        return [alert.name for alert in self.delivered]

    def silenced_names(self) -> list[str]:
        return [alert.name for alert, _ in self.silenced]


def matches(silence: Silence, alert: Alert) -> bool:
    """True if every matcher label is present on the alert with the same value."""
    for label, value in silence.matchers.items():
        if alert.labels.get(label) != value:
            return False
    return True


def is_active(silence: Silence, now: float) -> bool:
    """A silence applies from starts_at until ends_at (forever if ends_at is None)."""
    if now < silence.starts_at:
        return False
    return silence.ends_at is None or now < silence.ends_at


def naive_create_silence(matchers: dict[str, str], starts_at: float, ends_at: float | None,
                         created_by: str, comment: str) -> Silence:
    """The original tool: accepts any matchers and an optional end time."""
    return Silence(dict(matchers), starts_at, ends_at, created_by, comment)


def validate_scope(matchers: dict[str, str]) -> None:
    """Reject matchers that do not pin one service on one cluster or instance."""
    if not matchers.get(REQUIRED_LABEL):
        raise SilenceRejected(f"silence must match a specific '{REQUIRED_LABEL}'")
    has_narrowing = False
    for label in NARROWING_LABELS:
        if matchers.get(label):
            has_narrowing = True
    if not has_narrowing:
        raise SilenceRejected(f"silence must also match one of {', '.join(NARROWING_LABELS)}")


def validate_window(starts_at: float, ends_at: float | None) -> None:
    """Reject silences with no end, a reversed window, or a window that is too long."""
    if ends_at is None:
        raise SilenceRejected("silence must have an end time")
    if ends_at <= starts_at:
        raise SilenceRejected("silence must end after it starts")
    if ends_at - starts_at > MAX_SILENCE_SECONDS:
        raise SilenceRejected(f"silence may last at most {MAX_SILENCE_SECONDS // HOUR} hours")


def create_silence(matchers: dict[str, str], starts_at: float, ends_at: float | None,
                   created_by: str, comment: str) -> Silence:
    """Create a silence only if it is narrowly scoped, has an owner, and expires soon."""
    validate_scope(matchers)
    validate_window(starts_at, ends_at)
    if not created_by or not comment:
        # WHY: whoever sees a hidden alert later must know who to ask and why.
        raise SilenceRejected("silence needs an owner and a comment")
    return Silence(dict(matchers), starts_at, ends_at, created_by, comment)


def find_silence(alert: Alert, silences: list[Silence]) -> Silence | None:
    """The first silence that is active when the alert fires and matches it."""
    if alert.labels.get(NEVER_SILENCED_LABEL) == "true":
        return None
    for silence in silences:
        if is_active(silence, alert.fired_at) and matches(silence, alert):
            return silence
    return None


def route_alerts(alerts: list[Alert], silences: list[Silence]) -> RoutingResult:
    """Deliver each alert to on-call unless a silence covers it; record what was hidden."""
    result = RoutingResult()
    for alert in alerts:
        silence = find_silence(alert, silences)
        if silence is None:
            result.delivered.append(alert)
        else:
            result.silenced.append((alert, silence))
    return result

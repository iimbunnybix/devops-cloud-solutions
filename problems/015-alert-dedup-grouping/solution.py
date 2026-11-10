"""Turn a flood of raw alerts into a few grouped, deduplicated notifications.

During a database outage every host evaluated its own alert rules and every
firing was sent straight to the pager, every evaluation cycle. One root cause
became hundreds of pages. This module groups alerts the way Prometheus
Alertmanager does: identical alerts are merged by a label fingerprint, alerts
that share the grouping labels (e.g. alertname + cluster) wait a short
``group_wait`` and go out as one notification, new alerts joining a group that
already paged are batched every ``group_interval``, and an unchanged group is
only re-sent after ``repeat_interval``. A group whose alerts all resolve keeps
its paging history until ``repeat_interval`` passes, so an alert that flaps
(fires, resolves, fires again) does not page again on every flap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GROUP_WAIT_S = 30.0          # collect the first burst before paging at all
GROUP_INTERVAL_S = 300.0     # how often to send updates when a group changes
REPEAT_INTERVAL_S = 4 * 3600.0  # re-page an unchanged, still-firing group
DEFAULT_GROUP_BY = ("alertname", "cluster")

Labels = dict[str, str]


@dataclass
class Notification:
    """One message sent to the pager."""
    sent_at: float
    group_key: tuple[tuple[str, str], ...]
    alert_fingerprints: list[str]


@dataclass
class AlertGroup:
    """Alerts that share the grouping labels, plus the group's paging history."""
    key: tuple[tuple[str, str], ...]
    created_at: float
    alerts: dict[str, Labels] = field(default_factory=dict)
    last_sent_at: float | None = None
    fingerprints_at_last_send: frozenset[str] = frozenset()


def fingerprint(labels: Labels) -> str:
    """Identify an alert by all of its labels, independent of label order.

    Two firings with the same labels are the same alert, so the second one is
    a duplicate rather than a new problem.
    """
    parts = []
    for name in sorted(labels):
        parts.append(f"{name}={labels[name]}")
    return ",".join(parts)


def naive_notifications(firings: list[tuple[float, Labels]]) -> list[Notification]:
    """Original behaviour: every firing of every alert pages someone."""
    pages = []
    for timestamp, labels in firings:
        pages.append(Notification(timestamp, (), [fingerprint(labels)]))
    return pages


class AlertGrouper:
    """Group, deduplicate and rate-limit alert notifications."""

    def __init__(self, group_by: tuple[str, ...] = DEFAULT_GROUP_BY,
                 group_wait_s: float = GROUP_WAIT_S,
                 group_interval_s: float = GROUP_INTERVAL_S,
                 repeat_interval_s: float = REPEAT_INTERVAL_S) -> None:
        self.group_by = group_by
        self.group_wait_s = group_wait_s
        self.group_interval_s = group_interval_s
        self.repeat_interval_s = repeat_interval_s
        self.groups: dict[tuple[tuple[str, str], ...], AlertGroup] = {}
        self.sent: list[Notification] = []

    def group_key(self, labels: Labels) -> tuple[tuple[str, str], ...]:
        """Pick the grouping labels; a missing label groups as an empty value."""
        key = []
        for name in self.group_by:
            key.append((name, labels.get(name, "")))
        return tuple(key)

    def receive(self, labels: Labels, now: float) -> None:
        """Record a firing alert. Repeated firings of one alert are merged."""
        key = self.group_key(labels)
        group = self.groups.get(key)
        if group is None:
            group = AlertGroup(key=key, created_at=now)
            self.groups[key] = group
        group.alerts[fingerprint(labels)] = labels

    def resolve(self, labels: Labels) -> None:
        """Remove an alert that stopped firing.

        An emptied group that never paged has no history worth keeping, so it
        is dropped. A group that already paged is kept (empty) so that if the
        alert fires again soon, group_interval / repeat_interval still apply.
        """
        key = self.group_key(labels)
        group = self.groups.get(key)
        if group is None:
            return
        group.alerts.pop(fingerprint(labels), None)
        if not group.alerts and group.last_sent_at is None:
            del self.groups[key]

    def flush(self, now: float) -> list[Notification]:
        """Send whatever notifications are due at ``now`` and return them."""
        self._forget_quiet_groups(now)
        due = []
        for group in self.groups.values():
            # An empty group has nothing firing, so there is nothing to page.
            if group.alerts and self._is_due(group, now):
                due.append(self._send(group, now))
        return due

    def _forget_quiet_groups(self, now: float) -> None:
        """Drop empty groups whose last page is older than repeat_interval.

        After that long, a re-fire would be re-paged anyway, so the history
        no longer changes any decision and only costs memory.
        """
        expired_keys = []
        for key, group in self.groups.items():
            if group.alerts or group.last_sent_at is None:
                continue
            if now - group.last_sent_at >= self.repeat_interval_s:
                expired_keys.append(key)
        for key in expired_keys:
            del self.groups[key]

    def _is_due(self, group: AlertGroup, now: float) -> bool:
        """Decide whether a group should page now."""
        if group.last_sent_at is None:
            # Wait a little so the rest of the burst lands in the same page.
            return now - group.created_at >= self.group_wait_s
        elapsed = now - group.last_sent_at
        changed = frozenset(group.alerts) != group.fingerprints_at_last_send
        if changed:
            # New or resolved members: tell on-call, but at most once per interval.
            return elapsed >= self.group_interval_s
        return elapsed >= self.repeat_interval_s

    def _send(self, group: AlertGroup, now: float) -> Notification:
        """Build one notification for the whole group and remember what was sent."""
        members = sorted(group.alerts)
        notification = Notification(now, group.key, members)
        group.last_sent_at = now
        group.fingerprints_at_last_send = frozenset(members)
        self.sent.append(notification)
        return notification

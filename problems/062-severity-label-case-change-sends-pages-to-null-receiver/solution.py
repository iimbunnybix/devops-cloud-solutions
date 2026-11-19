"""Make sure every alert reaches a human, even if its labels are spelled oddly.

Alert routing matched labels exactly: ``severity="critical"`` paged the
on-call engineer, ``severity="info"`` went to a "null" receiver that
discards alerts, and anything that matched no route also fell through to
that "null" default. A logging library upgrade made the checkout service
emit ``severity="Critical"``. It matched no route, landed in "null", and a
three-hour checkout outage paged nobody.

The fix normalises the labels that routing depends on before matching,
rejects severities outside the known set, and sends anything that still
matches no route to a real "unrouted alerts" receiver. Discarding an alert
now requires an explicit route. A CI check lists alerts from a sample that
would not be routed normally.
"""
from __future__ import annotations

from dataclasses import dataclass

NULL_RECEIVER = "null"                    # discards alerts
UNROUTED_RECEIVER = "oncall-unrouted"     # real people, low urgency
VALID_SEVERITIES = {"critical", "warning", "info"}
# Labels that routes match on; their values are compared case-insensitively.
ROUTING_LABELS = ("severity", "team")


@dataclass(frozen=True)
class Route:
    """Send alerts whose labels equal all ``matchers`` to ``receiver``."""
    matchers: dict[str, str]
    receiver: str


@dataclass(frozen=True)
class RoutingDecision:
    receiver: str
    reason: str


def first_matching_route(labels: dict[str, str], routes: list[Route]) -> Route | None:
    """The first route whose matchers all equal the alert's labels."""
    for route in routes:
        matched = True
        for label, expected in route.matchers.items():
            if labels.get(label) != expected:
                matched = False
                break
        if matched:
            return route
    return None


def naive_route(labels: dict[str, str], routes: list[Route]) -> str:
    """The original config: exact match, unmatched alerts go to "null"."""
    route = first_matching_route(labels, routes)
    if route is None:
        return NULL_RECEIVER
    return route.receiver


def normalize_labels(labels: dict[str, str]) -> dict[str, str]:
    """Lower-case and trim the values routing depends on; keep others as sent."""
    normalized = dict(labels)
    for label in ROUTING_LABELS:
        if label in normalized:
            normalized[label] = normalized[label].strip().lower()
    return normalized


def route_alert(labels: dict[str, str], routes: list[Route]) -> RoutingDecision:
    """Route an alert so that it can only be dropped by an explicit rule."""
    normalized = normalize_labels(labels)
    severity = normalized.get("severity", "")
    if severity not in VALID_SEVERITIES:
        # A malformed alert is still a signal something may be wrong;
        # show it to a person instead of guessing how urgent it is.
        return RoutingDecision(UNROUTED_RECEIVER, f"invalid severity {labels.get('severity')!r}")
    route = first_matching_route(normalized, routes)
    if route is None:
        return RoutingDecision(UNROUTED_RECEIVER, "no route matched")
    return RoutingDecision(route.receiver, "matched route")


def find_unrouted(sample_alerts: list[dict[str, str]], routes: list[Route]) -> list[str]:
    """CI check: describe every sample alert that would not be routed normally.

    Run it with one example alert per alerting rule, so a typo in a rule or a
    route is caught at review time instead of during an outage.
    """
    problems = []
    for labels in sample_alerts:
        decision = route_alert(labels, routes)
        if decision.receiver == UNROUTED_RECEIVER:
            name = labels.get("alertname", "<no alertname>")
            problems.append(f"{name}: {decision.reason}")
    return problems

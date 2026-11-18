"""Measure availability from user traffic only, so health checks cannot hide an outage.

The availability SLI was "non-5xx requests / all requests" over the access log.
Load balancer health checks and uptime probes made up most of that log and always
returned 200, so when checkout failed for half of its users the SLI still read
99.5% and the 99% SLO alert stayed silent. This module keeps that original
calculation for comparison, then excludes synthetic traffic before computing the
SLI and also reports availability per route, so one broken endpoint stands out.
"""
from __future__ import annotations

from dataclasses import dataclass

# Paths that only probes call; they say nothing about what users experience.
PROBE_PATHS = frozenset({"/healthz", "/readyz", "/livez", "/ping"})
# Traffic sources tagged by the edge proxy for known non-user callers.
SYNTHETIC_SOURCES = frozenset({"lb-health-check", "uptime-probe", "kubelet"})
# Server errors count against the SLI; 4xx are the caller's fault.
FIRST_SERVER_ERROR = 500
SLO_TARGET_PERCENT = 99.0


@dataclass(frozen=True)
class Request:
    """One access-log line, reduced to what the SLI needs."""
    path: str
    status: int
    source: str = "user"


@dataclass(frozen=True)
class Availability:
    """Good and total counts for one slice of traffic."""
    good: int
    total: int

    def percent(self) -> float | None:
        """None when there is no traffic: no data is not the same as 100%."""
        if self.total == 0:
            return None
        return 100.0 * self.good / self.total


def is_good(request: Request) -> bool:
    """A request is good unless the server failed it."""
    return request.status < FIRST_SERVER_ERROR


def is_synthetic(request: Request) -> bool:
    """True for health checks and probes, by path or by the proxy's source tag."""
    if request.source in SYNTHETIC_SOURCES:
        return True
    return request.path in PROBE_PATHS


def count(requests: list[Request]) -> Availability:
    """Count good and total requests in a list."""
    good = 0
    for request in requests:
        if is_good(request):
            good += 1
    return Availability(good=good, total=len(requests))


def naive_availability(requests: list[Request]) -> Availability:
    """The original SLI: every line in the access log counts, probes included."""
    return count(requests)


def user_requests(requests: list[Request]) -> list[Request]:
    """Drop synthetic traffic so only real callers are measured."""
    kept: list[Request] = []
    for request in requests:
        if not is_synthetic(request):
            kept.append(request)
    return kept


def user_availability(requests: list[Request]) -> Availability:
    """The fixed SLI: availability as experienced by real callers."""
    return count(user_requests(requests))


def availability_by_route(requests: list[Request]) -> dict[str, Availability]:
    """Per-path availability of user traffic, so a single broken route is visible."""
    by_path: dict[str, list[Request]] = {}
    for request in user_requests(requests):
        by_path.setdefault(request.path, []).append(request)
    result: dict[str, Availability] = {}
    for path in sorted(by_path):
        result[path] = count(by_path[path])
    return result


def slo_breached(availability: Availability, target_percent: float = SLO_TARGET_PERCENT) -> bool:
    """True if availability is below target; no traffic is not reported as a breach."""
    percent = availability.percent()
    if percent is None:
        return False
    return percent < target_percent

"""Synthetic uptime check that cannot be fooled by a redirect to a 200 page.

When the checkout database failed, the web app's error middleware redirected
every request (302) to a static `/maintenance` page that returned 200 OK. The
uptime monitor followed redirects and only looked at the final status code,
so it reported the site as up for the whole outage. This checker does not
follow redirects by default, treats any unexpected status as down, and also
requires a content marker in the body so that "some page answered" is never
mistaken for "the right page answered".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

HTTP_OK = 200
MAX_REDIRECTS = 5


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""


class Fetcher(Protocol):
    def get(self, url: str) -> HttpResponse: ...


@dataclass
class CheckResult:
    healthy: bool
    reason: str
    final_url: str


def naive_check(fetcher: Fetcher, url: str) -> CheckResult:
    """Original monitor: follow every redirect, healthy if the last answer is 200."""
    current_url = url
    response = fetcher.get(current_url)
    for _ in range(MAX_REDIRECTS):
        if response.status not in (301, 302, 303, 307, 308):
            break
        current_url = response.headers["Location"]
        response = fetcher.get(current_url)
    healthy = response.status == HTTP_OK
    return CheckResult(healthy, f"status {response.status}", current_url)


@dataclass(frozen=True)
class CheckSpec:
    """What 'up' means for one endpoint, written down explicitly."""
    url: str
    expected_status: int = HTTP_OK
    # A string only the real page contains, e.g. '"status":"ok"' from /healthz.
    body_must_contain: str = ""


def check(fetcher: Fetcher, spec: CheckSpec) -> CheckResult:
    """Healthy only if the URL itself answers with the expected status and content.

    Redirects are not followed: a health endpoint that suddenly redirects is
    itself a sign that something is wrong (auth, maintenance mode, misrouting).
    """
    response = fetcher.get(spec.url)
    if response.status != spec.expected_status:
        location = response.headers.get("Location", "")
        detail = f" to {location}" if location else ""
        return CheckResult(False, f"status {response.status}{detail}", spec.url)
    if spec.body_must_contain and spec.body_must_contain not in response.body:
        return CheckResult(False, "expected content missing", spec.url)
    return CheckResult(True, "ok", spec.url)


def availability(results: list[CheckResult]) -> float:
    """Share of checks that were healthy, as the dashboard shows it."""
    if not results:
        return 0.0
    healthy_count = 0
    for result in results:
        if result.healthy:
            healthy_count += 1
    return healthy_count / len(results)

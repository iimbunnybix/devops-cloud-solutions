"""An auto-remediation bot restarted a broken service forever and never paged.

The bot restarted the search service whenever its health check failed. That
fixed the occasional stuck process. When the real cause was a full data disk,
the restart could not help: the bot restarted the service every minute for
hours, each restart dropped in-flight requests and cold-started caches, and
because the bot "handled" every failure, no human was ever paged. The fix
gives remediation a budget: at most a few restarts in a time window. When
restarts stop helping, the bot stops, pages once with what it tried, and
waits for a human.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

SIMULATED_MINUTES = 120
FAULT_STARTS_AT_MINUTE = 10
MAX_RESTARTS_PER_WINDOW = 3
RESTART_WINDOW_MINUTES = 30


@dataclass
class FakeService:
    """A service with one fault starting at ``fault_minute``.

    A transient fault (stuck process) is cured by a restart; a persistent
    fault (disk full, bad config) is not.
    """

    persistent_fault: bool
    fault_minute: int = FAULT_STARTS_AT_MINUTE
    restarted_since_fault: bool = False
    restarts: int = 0

    def healthy(self, minute: int) -> bool:
        if minute < self.fault_minute:
            return True
        if self.persistent_fault:
            return False
        return self.restarted_since_fault

    def restart(self, minute: int) -> None:
        self.restarts += 1
        if minute >= self.fault_minute:
            self.restarted_since_fault = True


@dataclass
class RunResult:
    restarts: int
    pages: list[str] = field(default_factory=list)
    unhealthy_minutes: int = 0


# ---------------------------------------------------------------- original
def run_naive_bot(service: FakeService) -> RunResult:
    """Original bot: restart on every failed health check, never escalate."""
    result = RunResult(restarts=0)
    for minute in range(SIMULATED_MINUTES):
        if not service.healthy(minute):
            result.unhealthy_minutes += 1
            service.restart(minute)
    result.restarts = service.restarts
    return result


# ---------------------------------------------------------------- the fix
class RemediationPolicy:
    """Restart budget per sliding window, then escalate to a human."""

    def __init__(self, max_restarts: int = MAX_RESTARTS_PER_WINDOW,
                 window_minutes: int = RESTART_WINDOW_MINUTES) -> None:
        self.max_restarts = max_restarts
        self.window_minutes = window_minutes
        self.restart_minutes: deque[int] = deque()
        self.escalated = False

    def _forget_old_restarts(self, minute: int) -> None:
        """Drop restarts that are outside the sliding window."""
        while self.restart_minutes and self.restart_minutes[0] <= minute - self.window_minutes:
            self.restart_minutes.popleft()

    def decide(self, minute: int, healthy: bool) -> str:
        """Return "ok", "restart", "page" or "wait" for this health check.

        Once we have paged, the bot keeps its hands off: a human is now
        debugging, and a restart in the middle would destroy evidence and
        reset their work.
        """
        if healthy:
            return "ok"
        if self.escalated:
            return "wait"
        self._forget_old_restarts(minute)
        if len(self.restart_minutes) < self.max_restarts:
            self.restart_minutes.append(minute)
            return "restart"
        self.escalated = True
        return "page"

    def acknowledge(self) -> None:
        """A human fixed the cause; give the bot its full budget back."""
        self.escalated = False
        self.restart_minutes.clear()


def run_budgeted_bot(service: FakeService, policy: RemediationPolicy) -> RunResult:
    """Same health checks, but remediation follows the policy."""
    result = RunResult(restarts=0)
    for minute in range(SIMULATED_MINUTES):
        healthy = service.healthy(minute)
        if not healthy:
            result.unhealthy_minutes += 1
        action = policy.decide(minute, healthy)
        if action == "restart":
            service.restart(minute)
        elif action == "page":
            # Tell the human what was already tried, so they skip that step.
            result.pages.append(
                f"minute {minute}: still unhealthy after {len(policy.restart_minutes)} "
                f"restarts in {policy.window_minutes} min; auto-restart paused")
    result.restarts = service.restarts
    return result

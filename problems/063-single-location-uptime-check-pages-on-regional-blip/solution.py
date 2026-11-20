"""Page on-call only when the site is down from several places, not one.

The external uptime monitor probed the site from a single location once a
minute and paged on the first failed probe. Any network blip between that
probe and the site (a transit provider rerouting, a flaky regional link)
looked exactly like an outage, so on-call was woken for incidents customers
never saw, and dozens of false pages a month taught people to ignore pages.

The fix probes from several locations every round and declares the site DOWN
only when at least a quorum of locations fail for several consecutive rounds.
Recovery likewise needs consecutive healthy rounds, and every verdict names
the locations that failed so on-call can tell a regional problem from a
global one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

UP = "UP"
DOWN = "DOWN"
DEFAULT_QUORUM = 3            # of 5 locations: a majority must agree
DEFAULT_FAILING_ROUNDS = 2    # consecutive failing rounds before paging
DEFAULT_RECOVERY_ROUNDS = 2   # consecutive healthy rounds before resolving

# One round of probes: location name -> True if that probe succeeded.
ProbeRound = dict[str, bool]


@dataclass
class NaiveChecker:
    """The original monitor: one location, one failed probe means DOWN."""
    location: str
    state: str = UP

    def observe(self, probes: ProbeRound) -> bool:
        """Record a round; return True if this round sends a page."""
        succeeded = probes.get(self.location, False)
        new_state = UP if succeeded else DOWN
        pages = new_state == DOWN and self.state == UP
        self.state = new_state
        return pages


@dataclass(frozen=True)
class Verdict:
    """The monitor's view after one round of probes."""
    state: str
    changed: bool
    failed_locations: list[str]

    @property
    def pages(self) -> bool:
        """True when this round should page on-call (just went DOWN)."""
        return self.changed and self.state == DOWN


@dataclass
class QuorumChecker:
    """Multi-location monitor with a quorum and consecutive-round thresholds."""
    locations: list[str]
    quorum: int = DEFAULT_QUORUM
    failing_rounds_to_alert: int = DEFAULT_FAILING_ROUNDS
    healthy_rounds_to_recover: int = DEFAULT_RECOVERY_ROUNDS
    state: str = UP
    _failing_streak: int = field(default=0, init=False)
    _healthy_streak: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        """Reject settings that would make the quorum meaningless."""
        if not 1 <= self.quorum <= len(self.locations):
            raise ValueError(f"quorum {self.quorum} must be between 1 and {len(self.locations)}")
        if self.failing_rounds_to_alert < 1 or self.healthy_rounds_to_recover < 1:
            raise ValueError("round thresholds must be at least 1")

    def failed_locations(self, probes: ProbeRound) -> list[str]:
        """Configured locations whose probe failed this round, sorted."""
        # A location with no result (its probe agent is down) is not counted
        # as a failure: the monitoring system's problem is not the site's.
        failed = []
        for location in self.locations:
            if location in probes and not probes[location]:
                failed.append(location)
        return sorted(failed)

    def observe(self, probes: ProbeRound) -> Verdict:
        """Record one round of probes and return the resulting verdict."""
        failed = self.failed_locations(probes)
        if len(failed) >= self.quorum:
            self._failing_streak += 1
            self._healthy_streak = 0
        else:
            self._healthy_streak += 1
            self._failing_streak = 0
        new_state = self._next_state()
        changed = new_state != self.state
        self.state = new_state
        return Verdict(new_state, changed, failed)

    def _next_state(self) -> str:
        """Flip state only after enough consecutive rounds agree."""
        if self.state == UP and self._failing_streak >= self.failing_rounds_to_alert:
            return DOWN
        if self.state == DOWN and self._healthy_streak >= self.healthy_rounds_to_recover:
            return UP
        return self.state


def count_pages_naive(checker: NaiveChecker, rounds: list[ProbeRound]) -> int:
    """How many pages the naive monitor sends over these rounds."""
    pages = 0
    for probes in rounds:
        if checker.observe(probes):
            pages += 1
    return pages


def count_pages_quorum(checker: QuorumChecker, rounds: list[ProbeRound]) -> int:
    """How many pages the quorum monitor sends over these rounds."""
    pages = 0
    for probes in rounds:
        if checker.observe(probes).pages:
            pages += 1
    return pages

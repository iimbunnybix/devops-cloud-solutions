"""Plan a DNS cutover so no resolver still sends users to the old server.

Before moving a hostname to a new IP, teams lower the record's TTL (say from
24 hours to 60 seconds) so the switch spreads quickly. But resolvers that
fetched the record *before* the TTL was lowered cache it for the OLD TTL.
Cutting over one hour after lowering the TTL leaves those resolvers on the
old IP for up to 23 more hours.

``safe_cutover_time`` waits until every answer given before the TTL change
has expired, and ``safe_retire_time`` says when the old server can be turned
off. ``CachingResolver`` is a small TTL cache used to prove both.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecordVersion:
    """One version of the A record, valid from ``start`` until the next one."""
    start: float        # seconds
    ip: str
    ttl: int            # seconds


class AuthoritativeZone:
    """The DNS provider: answers with the record version valid at time t."""

    def __init__(self, history: list[RecordVersion]) -> None:
        if not history:
            raise ValueError("history must not be empty")
        self.history = sorted(history, key=lambda v: v.start)

    def answer(self, now: float) -> RecordVersion:
        """Latest version whose start is not after ``now``."""
        current = self.history[0]
        for version in self.history:
            if version.start <= now:
                current = version
        return current


class CachingResolver:
    """A recursive resolver (ISP, office, cloud) that honours the TTL it got."""

    def __init__(self) -> None:
        self.cached: RecordVersion | None = None
        self.expires_at = float("-inf")

    def lookup(self, now: float, zone: AuthoritativeZone) -> str:
        """Return the cached IP, refreshing from the zone once it expired."""
        if self.cached is None or now >= self.expires_at:
            self.cached = zone.answer(now)
            # WHY: the TTL is fixed when the answer is fetched; a later TTL
            # change at the provider does not shorten this cache entry.
            self.expires_at = now + self.cached.ttl
        return self.cached.ip


def naive_cutover_time(ttl_lowered_at: float, new_ttl: int, margin: float) -> float:
    """The original plan: wait the NEW TTL plus a margin after lowering it."""
    return ttl_lowered_at + new_ttl + margin


def safe_cutover_time(history: list[RecordVersion], ttl_lowered_at: float) -> float:
    """Earliest time at which no answer handed out before the TTL change is cached.

    An answer from a version can be fetched until that version ends (the next
    version starts, or the TTL change) and then lives for that version's TTL.
    """
    latest_expiry = ttl_lowered_at
    ordered = sorted(history, key=lambda v: v.start)
    for index, version in enumerate(ordered):
        if version.start >= ttl_lowered_at:
            continue
        next_start = ordered[index + 1].start if index + 1 < len(ordered) else ttl_lowered_at
        served_until = min(next_start, ttl_lowered_at)
        latest_expiry = max(latest_expiry, served_until + version.ttl)
    return latest_expiry


def safe_retire_time(cutover_at: float, ttl_at_cutover: int) -> float:
    """The old server must keep serving until the last pre-cutover answer expires."""
    return cutover_at + ttl_at_cutover

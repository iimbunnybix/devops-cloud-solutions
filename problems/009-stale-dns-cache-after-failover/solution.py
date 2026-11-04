"""DNS caching that follows a database failover instead of pinning the dead primary.

Managed databases fail over by pointing a DNS name (e.g. ``db.prod.internal``)
at the new primary with a short TTL. The original application resolved the
name once and cached the IP for the life of the process (as the JVM does with
``networkaddress.cache.ttl=-1``, or as a connection pool does when it stores
the IP), so after a failover it kept connecting to the old, dead primary until
someone restarted it.

``TtlResolver`` honours the record's TTL (clamped to sane bounds), forgets an
entry when a connection to it fails, rate-limits re-resolution so a broken
backend cannot turn into a DNS flood, and serves the last known answer for a
bounded time if DNS itself is down.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class DnsError(Exception):
    pass


class FakeDns:
    """Authoritative DNS stub: name -> (ip, ttl). Counts queries; can be taken down."""

    def __init__(self) -> None:
        self.records: dict[str, tuple[str, int]] = {}
        self.queries = 0
        self.down = False

    def set(self, name: str, ip: str, ttl: int) -> None:
        self.records[name] = (ip, ttl)

    def query(self, name: str) -> tuple[str, int]:
        self.queries += 1
        if self.down:
            raise DnsError("SERVFAIL")
        if name not in self.records:
            raise DnsError("NXDOMAIN")
        return self.records[name]


class FakeNetwork:
    """Which IPs currently accept connections."""

    def __init__(self, up: set[str]) -> None:
        self.up = set(up)

    def connect(self, ip: str) -> str:
        if ip not in self.up:
            raise ConnectionError(f"connect to {ip} timed out")
        return ip


class ForeverResolver:
    """Original behaviour: resolve once, cache until the process restarts."""

    def __init__(self, dns: FakeDns) -> None:
        self.dns = dns
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self._cache:
            self._cache[name] = self.dns.query(name)[0]
        return self._cache[name]

    def invalidate(self, name: str) -> None:  # the original code had no such hook
        pass


@dataclass
class _Entry:
    ip: str
    expires_at: float
    fetched_at: float


class TtlResolver:
    """TTL-respecting resolver cache.

    Args:
        min_ttl / max_ttl: clamp the record TTL. ``max_ttl`` bounds how long a
            wrong answer can survive; ``min_ttl`` stops TTL=0 records causing a
            lookup per request.
        stale_grace: if DNS fails, keep serving an expired answer for this long.
        min_refresh_interval: never query DNS for one name more often than this,
            even when connections keep failing and invalidating it.
    """

    def __init__(self, dns: FakeDns, clock: FakeClock, *, min_ttl: float = 1.0,
                 max_ttl: float = 60.0, stale_grace: float = 300.0,
                 min_refresh_interval: float = 1.0) -> None:
        self.dns = dns
        self.clock = clock
        self.min_ttl = min_ttl
        self.max_ttl = max_ttl
        self.stale_grace = stale_grace
        self.min_refresh_interval = min_refresh_interval
        self._cache: dict[str, _Entry] = {}

    def get(self, name: str) -> str:
        now = self.clock.now()
        entry = self._cache.get(name)
        if entry and now < entry.expires_at:
            return entry.ip
        if entry and now - entry.fetched_at < self.min_refresh_interval:
            return entry.ip  # just refreshed; don't hammer DNS
        try:
            ip, ttl = self.dns.query(name)
        except DnsError:
            if entry and now - entry.expires_at <= self.stale_grace:
                return entry.ip  # serve stale rather than fail every request
            raise
        ttl = min(self.max_ttl, max(self.min_ttl, ttl))
        self._cache[name] = _Entry(ip, now + ttl, now)
        return ip

    def invalidate(self, name: str) -> None:
        """Mark an entry expired (kept for stale serving and rate limiting)."""
        entry = self._cache.get(name)
        if entry:
            entry.expires_at = min(entry.expires_at, self.clock.now())


class DbClient:
    """Connects by name. On a connection failure, re-resolves once and retries if the IP changed."""

    def __init__(self, name: str, resolver, network: FakeNetwork, *,
                 invalidate_on_failure: bool = True, retry_on_new_ip: bool = True) -> None:
        self.name = name
        self.resolver = resolver
        self.network = network
        self.invalidate_on_failure = invalidate_on_failure
        self.retry_on_new_ip = retry_on_new_ip

    def connect(self) -> str:
        ip = self.resolver.get(self.name)
        try:
            return self.network.connect(ip)
        except ConnectionError:
            if not self.invalidate_on_failure:
                raise
            self.resolver.invalidate(self.name)
            if not self.retry_on_new_ip:
                raise
            new_ip: Optional[str] = self.resolver.get(self.name)
            if new_ip == ip:
                raise
            return self.network.connect(new_ip)


def run_traffic(client: DbClient, clock: FakeClock, seconds: int, every: float = 1.0) -> int:
    """Make one connection attempt every ``every`` seconds; return how many failed."""
    failures = 0
    for _ in range(int(seconds / every)):
        try:
            client.connect()
        except (ConnectionError, DnsError):
            failures += 1
        clock.advance(every)
    return failures

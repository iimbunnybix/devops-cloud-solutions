"""Stop a busy client from running out of local ports to its upstream.

A service called one upstream address with a new TCP connection per request
and closed it afterwards. The side that closes first keeps the connection in
TIME_WAIT for 60 seconds on Linux, and the local port of that connection is
not reused for the same destination meanwhile. With about 28,000 ephemeral
ports, anything above ~470 requests per second to one destination runs out
of ports and connect() fails with EADDRNOTAVAIL.

The fix reuses a small pool of keep-alive connections, so the number of
ports in use depends on concurrency, not on request rate. A helper also
computes the safe request rate, which is what an alert should watch.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

# Linux defaults: net.ipv4.ip_local_port_range = 32768 60999, TIME_WAIT = 60 s.
EPHEMERAL_PORT_LOW = 32768
EPHEMERAL_PORT_HIGH = 60999
TIME_WAIT_SECONDS = 60


class PortExhausted(Exception):
    """connect() failed: EADDRNOTAVAIL, no free local port for this destination."""


class EphemeralPorts:
    """Local ports towards ONE destination address, with TIME_WAIT on close."""

    def __init__(self, low: int = EPHEMERAL_PORT_LOW, high: int = EPHEMERAL_PORT_HIGH,
                 time_wait_seconds: int = TIME_WAIT_SECONDS) -> None:
        self._free = deque(range(low, high + 1))
        # Every port waits the same time, so they expire in the order they closed.
        self._time_wait: deque[tuple[int, int]] = deque()
        self._time_wait_seconds = time_wait_seconds
        self.in_use = 0

    @property
    def in_time_wait(self) -> int:
        return len(self._time_wait)

    def reclaim(self, now: int) -> None:
        """Return ports whose TIME_WAIT has finished to the free list."""
        while self._time_wait and self._time_wait[0][0] <= now:
            _, port = self._time_wait.popleft()
            self._free.append(port)

    def connect(self, now: int) -> int:
        """Take a free local port, or fail like the kernel does."""
        self.reclaim(now)
        if not self._free:
            raise PortExhausted("EADDRNOTAVAIL: cannot assign requested address")
        self.in_use += 1
        return self._free.popleft()

    def close(self, port: int, now: int) -> None:
        """Active close: the port sits in TIME_WAIT before it can be reused."""
        self.in_use -= 1
        self._time_wait.append((now + self._time_wait_seconds, port))


@dataclass
class RunReport:
    succeeded: int = 0
    failed: int = 0
    first_failure_second: int | None = None
    peak_ports_in_time_wait: int = 0

    def record_failure(self, second: int) -> None:
        self.failed += 1
        if self.first_failure_second is None:
            self.first_failure_second = second


# --- the original, buggy client ------------------------------------------------
def naive_run(ports: EphemeralPorts, requests_per_second: int, seconds: int) -> RunReport:
    """Original client: one new connection per request, closed right after."""
    report = RunReport()
    for second in range(seconds):
        for _ in range(requests_per_second):
            try:
                port = ports.connect(second)
            except PortExhausted:
                report.record_failure(second)
                continue
            report.succeeded += 1
            ports.close(port, second)
        report.peak_ports_in_time_wait = max(report.peak_ports_in_time_wait, ports.in_time_wait)
    return report


# --- the fix -------------------------------------------------------------------
class KeepAlivePool:
    """Reuses open connections; opens a new one only when all are busy."""

    def __init__(self, ports: EphemeralPorts, max_connections: int) -> None:
        self._ports = ports
        self.max_connections = max_connections
        self.open_connections: list[int] = []

    def ensure_open(self, needed: int, now: int) -> int:
        """Open connections up to ``needed`` (capped); return how many are open."""
        target = min(needed, self.max_connections)
        while len(self.open_connections) < target:
            self.open_connections.append(self._ports.connect(now))
        return len(self.open_connections)


def connections_needed(requests_per_second: int, request_seconds: float) -> int:
    """Little's law: concurrent requests = rate x time each one takes."""
    return math.ceil(requests_per_second * request_seconds)


def pooled_run(ports: EphemeralPorts, requests_per_second: int, seconds: int,
               request_seconds: float, max_connections: int) -> RunReport:
    """Fixed client: each connection serves 1/request_seconds requests per second."""
    report = RunReport()
    pool = KeepAlivePool(ports, max_connections)
    per_connection_capacity = int(1 / request_seconds)
    for second in range(seconds):
        open_count = pool.ensure_open(connections_needed(requests_per_second, request_seconds), second)
        capacity = open_count * per_connection_capacity
        served = min(requests_per_second, capacity)
        report.succeeded += served
        for _ in range(requests_per_second - served):
            report.record_failure(second)
    return report


def max_sustainable_rate(port_count: int, time_wait_seconds: int) -> float:
    """Highest new-connection rate to one destination before ports run out."""
    return port_count / time_wait_seconds

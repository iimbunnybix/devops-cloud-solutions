"""Stop random 502s caused by a backend keep-alive timeout shorter than the load balancer's.

The load balancer keeps idle TCP connections to each backend and reuses them for
new requests for up to 60 seconds. The backend closed idle connections after 5
seconds. When a request was sent on a connection at the same moment the backend
closed it (before the backend's FIN reached the balancer), the request hit a dead
socket and the client got a 502. This module simulates that pool, and checks the
fix: the backend must keep idle connections open longer than the balancer, so the
balancer is always the side that closes them and the race cannot happen.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# Typical managed load balancer idle timeout (AWS ALB default: 60 s).
LB_IDLE_TIMEOUT_MS = 60_000
# Node.js http.Server keepAliveTimeout default: 5 s.
DEFAULT_BACKEND_KEEPALIVE_MS = 5_000
# How long the backend's FIN takes to reach the balancer (one network hop).
FIN_DELAY_MS = 5
# Extra headroom so clock jitter and slow FIN delivery never reorder the closes.
SAFETY_MARGIN_MS = 5_000


@dataclass
class Connection:
    """One pooled balancer-to-backend TCP connection."""
    busy_until_ms: int = 0

    def backend_closes_at(self, backend_keepalive_ms: int) -> int:
        """The backend starts its idle timer when it finishes the last response."""
        return self.busy_until_ms + backend_keepalive_ms


@dataclass
class PoolStats:
    """What the clients saw over a simulation run."""
    served: int = 0
    bad_gateway: int = 0
    connections_opened: int = 0


class LoadBalancerPool:
    """Idle-connection pool on the balancer side, with one backend behind it."""

    def __init__(self, lb_idle_ms: int, backend_keepalive_ms: int,
                 fin_delay_ms: int = FIN_DELAY_MS) -> None:
        self.lb_idle_ms = lb_idle_ms
        self.backend_keepalive_ms = backend_keepalive_ms
        self.fin_delay_ms = fin_delay_ms
        self.connections: list[Connection] = []
        self.stats = PoolStats()

    def _drop_connections_known_closed(self, now_ms: int) -> None:
        """Remove connections the balancer knows are gone at ``now_ms``."""
        still_open: list[Connection] = []
        for connection in self.connections:
            if connection.busy_until_ms > now_ms:
                still_open.append(connection)
                continue
            idle_ms = now_ms - connection.busy_until_ms
            # The balancer closes its own idle connections; that close is never a race.
            closed_by_lb = idle_ms >= self.lb_idle_ms
            # A backend close is only known once its FIN has travelled back.
            fin_arrived = now_ms >= (connection.backend_closes_at(self.backend_keepalive_ms)
                                     + self.fin_delay_ms)
            if not closed_by_lb and not fin_arrived:
                still_open.append(connection)
        self.connections = still_open

    def _pick_idle_connection(self, now_ms: int) -> Connection | None:
        """Most recently used idle connection, as most balancers prefer warm ones."""
        best: Connection | None = None
        for connection in self.connections:
            if connection.busy_until_ms > now_ms:
                continue
            if best is None or connection.busy_until_ms > best.busy_until_ms:
                best = connection
        return best

    def handle_request(self, now_ms: int, duration_ms: int) -> bool:
        """Send one request; return False if the client got a 502."""
        self._drop_connections_known_closed(now_ms)
        connection = self._pick_idle_connection(now_ms)
        if connection is not None and now_ms >= connection.backend_closes_at(self.backend_keepalive_ms):
            # The backend already closed this socket but its FIN is still in flight:
            # the request gets a reset and the balancer answers 502.
            self.connections.remove(connection)
            self.stats.bad_gateway += 1
            return False
        if connection is None:
            connection = Connection()
            self.connections.append(connection)
            self.stats.connections_opened += 1
        connection.busy_until_ms = now_ms + duration_ms
        self.stats.served += 1
        return True


def run_traffic(pool: LoadBalancerPool, arrival_times_ms: list[int],
                duration_ms: int) -> PoolStats:
    """Replay requests at the given times through the pool."""
    for arrival_ms in arrival_times_ms:
        pool.handle_request(arrival_ms, duration_ms)
    return pool.stats


def bursty_low_traffic(request_count: int, max_gap_ms: int, seed: int) -> list[int]:
    """Arrival times with random gaps, like an internal API called every few seconds."""
    rng = random.Random(seed)
    times: list[int] = []
    now_ms = 0
    for _ in range(request_count):
        now_ms += rng.randint(0, max_gap_ms)
        times.append(now_ms)
    return times


def keepalive_problems(lb_idle_ms: int, backend_keepalive_ms: int,
                       margin_ms: int = SAFETY_MARGIN_MS) -> list[str]:
    """Config check for CI: the backend must outlive the balancer's idle timeout."""
    problems: list[str] = []
    if backend_keepalive_ms <= lb_idle_ms:
        problems.append(
            f"backend keep-alive {backend_keepalive_ms} ms <= load balancer idle "
            f"{lb_idle_ms} ms: the backend can close a connection the balancer is reusing")
    elif backend_keepalive_ms < lb_idle_ms + margin_ms:
        problems.append(
            f"backend keep-alive {backend_keepalive_ms} ms is within {margin_ms} ms of the "
            f"load balancer idle {lb_idle_ms} ms: keep a safety margin")
    return problems


def safe_backend_keepalive_ms(lb_idle_ms: int, margin_ms: int = SAFETY_MARGIN_MS) -> int:
    """The keep-alive timeout to configure on the backend for a given balancer."""
    return lb_idle_ms + margin_ms

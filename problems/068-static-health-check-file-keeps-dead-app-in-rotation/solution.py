"""Make the load balancer health check ask the application, not the web server.

Each instance runs nginx in front of the app. The load balancer checked
``/health.html``, a static file nginx served from disk. When the app process
crashed or hung, nginx kept answering that file with 200, so the instance
stayed in rotation and every request routed to it got a 502.

The fix points the check at ``/healthz``, which nginx proxies to the app.
The app answers 200 only when its process is up and it is ready (warmed up,
not draining). It does not check the database, so one dependency outage
cannot pull every instance out at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

HTTP_OK = 200
HTTP_BAD_GATEWAY = 502
HTTP_UNAVAILABLE = 503
# Typical load balancer defaults: 3 failures to remove, 2 successes to re-add.
UNHEALTHY_THRESHOLD = 3
HEALTHY_THRESHOLD = 2


@dataclass
class Instance:
    name: str
    nginx_up: bool = True
    app_up: bool = True
    warmed_up: bool = True
    draining: bool = False


def serve_request(instance: Instance) -> int:
    """A real user request: nginx proxies it to the app."""
    if instance.nginx_up and instance.app_up:
        return HTTP_OK
    return HTTP_BAD_GATEWAY


# --- the original, buggy check ---------------------------------------------------
def naive_health_check(instance: Instance) -> int:
    """Original: GET /health.html, a static file nginx serves itself."""
    return HTTP_OK if instance.nginx_up else HTTP_BAD_GATEWAY


# --- the fix -------------------------------------------------------------------
def app_health_check(instance: Instance) -> int:
    """GET /healthz, proxied to the app, which reports its own readiness."""
    if not instance.nginx_up or not instance.app_up:
        # nginx cannot reach the app process: the same 502 a user would get.
        return HTTP_BAD_GATEWAY
    if not instance.warmed_up or instance.draining:
        # Alive but should not get traffic yet (startup) or any more (shutdown).
        return HTTP_UNAVAILABLE
    return HTTP_OK


@dataclass
class TargetState:
    in_rotation: bool = True
    consecutive_failures: int = 0
    consecutive_successes: int = 0


@dataclass
class LoadBalancer:
    instances: list[Instance]
    health_check: Callable[[Instance], int]
    states: dict[str, TargetState] = field(default_factory=dict)
    _next_index: int = 0

    def __post_init__(self) -> None:
        for instance in self.instances:
            self.states[instance.name] = TargetState()

    def run_health_checks(self) -> None:
        """One check round; thresholds stop a single blip from flapping targets."""
        for instance in self.instances:
            state = self.states[instance.name]
            if self.health_check(instance) == HTTP_OK:
                state.consecutive_successes += 1
                state.consecutive_failures = 0
                if state.consecutive_successes >= HEALTHY_THRESHOLD:
                    state.in_rotation = True
            else:
                state.consecutive_failures += 1
                state.consecutive_successes = 0
                if state.consecutive_failures >= UNHEALTHY_THRESHOLD:
                    state.in_rotation = False

    def targets(self) -> list[Instance]:
        """Instances that get traffic. With none healthy, fail open to all."""
        healthy = [instance for instance in self.instances if self.states[instance.name].in_rotation]
        return healthy if healthy else list(self.instances)

    def route(self, request_count: int) -> list[int]:
        """Round-robin ``request_count`` requests; return their status codes."""
        statuses = []
        targets = self.targets()
        for _ in range(request_count):
            instance = targets[self._next_index % len(targets)]
            self._next_index += 1
            statuses.append(serve_request(instance))
        return statuses


def count_errors(balancer: LoadBalancer, intervals: int, requests_per_interval: int) -> int:
    """Alternate check rounds and traffic; return how many requests failed."""
    errors = 0
    for _ in range(intervals):
        balancer.run_health_checks()
        for status in balancer.route(requests_per_interval):
            if status != HTTP_OK:
                errors += 1
    return errors

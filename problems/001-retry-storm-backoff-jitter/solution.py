"""Retry with exponential backoff + full jitter, deadline, and a circuit breaker."""
from __future__ import annotations

import random
import threading
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """Timeouts, 503s, connection resets."""


class CircuitOpenError(Exception):
    pass


def backoff_delays(base: float, cap: float, attempts: int, rng: random.Random | None = None,
                   jitter: bool = True) -> list[float]:
    rng = rng or random.Random()
    out = []
    for n in range(attempts):
        ceiling = min(cap, base * 2 ** n)
        out.append(rng.uniform(0, ceiling) if jitter else ceiling)
    return out


def retry(fn: Callable[[], T], *, max_attempts: int = 5, base: float = 0.1, cap: float = 5.0,
          deadline_s: float = 20.0, sleep: Callable[[float], None] = time.sleep,
          clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None) -> T:
    rng = rng or random.Random()
    start = clock()
    for attempt in range(max_attempts):
        try:
            return fn()
        except RetryableError:
            if attempt == max_attempts - 1:
                raise
            delay = rng.uniform(0, min(cap, base * 2 ** attempt))
            if clock() - start + delay > deadline_s:
                raise
            sleep(delay)
    raise AssertionError("unreachable")


class CircuitBreaker:
    """CLOSED -> (N failures) -> OPEN -> (cool-down) -> HALF_OPEN -> success: CLOSED / fail: OPEN"""

    def __init__(self, failure_threshold: int = 5, reset_after_s: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_after_s = reset_after_s
        self._clock = clock
        self.state = "CLOSED"
        self._failures = 0
        self._opened_at = 0.0
        # Guards state changes so concurrent callers see one consistent breaker.
        self._lock = threading.Lock()
        self._trial_in_flight = False

    def call(self, fn: Callable[[], T]) -> T:
        self._admit()
        try:
            result = fn()
        except RetryableError:
            self._record_retryable_failure()
            raise
        except Exception:
            # A non-retryable error (e.g. a 400) means the dependency answered,
            # so it is up; never leave the breaker stuck in HALF_OPEN.
            self._finish_trial_without_retryable_failure()
            raise
        self._record_success()
        return result

    def _admit(self) -> None:
        """Decide under the lock whether this call may reach the dependency.

        After the cool-down exactly one caller becomes the trial request;
        everyone else keeps failing fast until that trial finishes.
        """
        with self._lock:
            if self.state == "HALF_OPEN" and self._trial_in_flight:
                raise CircuitOpenError("trial request in flight, failing fast")
            if self.state == "OPEN":
                if self._clock() - self._opened_at < self.reset_after_s:
                    raise CircuitOpenError("dependency unavailable, failing fast")
                self.state = "HALF_OPEN"
                self._trial_in_flight = True

    def _record_retryable_failure(self) -> None:
        """Count a failure; a failed trial or too many failures (re)open the breaker."""
        with self._lock:
            self._failures += 1
            if self.state == "HALF_OPEN" or self._failures >= self.failure_threshold:
                self.state, self._opened_at = "OPEN", self._clock()
            self._trial_in_flight = False

    def _finish_trial_without_retryable_failure(self) -> None:
        """Close a HALF_OPEN breaker; in CLOSED leave the failure count alone."""
        with self._lock:
            if self.state == "HALF_OPEN":
                self.state, self._failures = "CLOSED", 0
            self._trial_in_flight = False

    def _record_success(self) -> None:
        """The dependency answered: close the breaker and reset counters."""
        with self._lock:
            self.state, self._failures = "CLOSED", 0
            self._trial_in_flight = False


def simulate_peak_load(n_clients: int, jitter: bool, seed: int = 0, recovery_t: float = 6.0) -> int:
    """Each client fails at t=0 and retries with backoff until the DB is back.
    Returns the max number of retries landing in any 100 ms bucket after recovery."""
    rng = random.Random(seed)
    buckets: dict[int, int] = {}
    for _ in range(n_clients):
        t = 0.0
        for d in backoff_delays(0.5, 8.0, 12, rng, jitter=jitter):
            t += d
            if t >= recovery_t:
                b = int(t * 10)
                buckets[b] = buckets.get(b, 0) + 1
                break
    return max(buckets.values())

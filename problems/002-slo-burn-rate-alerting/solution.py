"""Multi-window, multi-burn-rate SLO alerting over per-minute counters."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    severity: str
    long_min: int
    short_min: int
    burn_rate: float


DEFAULT_RULES = (
    Rule("page", 60, 5, 14.4),
    Rule("page", 360, 30, 6.0),
    Rule("ticket", 3 * 24 * 60, 360, 1.0),
)


class BurnRateAlerter:
    def __init__(self, slo: float = 0.999, rules=DEFAULT_RULES):
        if not 0 < slo < 1:
            raise ValueError("slo must be between 0 and 1")
        self.budget = 1 - slo
        self.rules = rules
        self._max_window = max(r.long_min for r in rules)
        self._minutes: deque = deque(maxlen=self._max_window)   # (requests, errors)

    def _burn(self, minutes: int) -> float:
        window = list(self._minutes)[-minutes:]
        req = sum(r for r, _ in window)
        err = sum(e for _, e in window)
        if req == 0:
            return 0.0
        return (err / req) / self.budget

    def observe_minute(self, requests: int, errors: int) -> list[str]:
        """Add one minute of data; return the severities currently firing."""
        if errors > requests or requests < 0:
            raise ValueError("invalid counters")
        self._minutes.append((requests, errors))
        firing = []
        for rule in self.rules:
            if len(self._minutes) < rule.short_min:
                continue
            if self._burn(rule.long_min) >= rule.burn_rate and self._burn(rule.short_min) >= rule.burn_rate:
                firing.append(rule.severity)
        return sorted(set(firing))


def static_threshold_alert(history: list[tuple[int, int]], threshold: float = 0.01, minutes: int = 5) -> bool:
    """The old rule, kept for comparison: error rate > 1% over the last 5 minutes."""
    window = history[-minutes:]
    req = sum(r for r, _ in window)
    return req > 0 and sum(e for _, e in window) / req > threshold

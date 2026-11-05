"""Warn before a burstable instance runs out of CPU credits and is throttled.

Burstable instances (for example AWS t3) earn CPU credits at a fixed rate
and spend them whenever CPU use is above a *baseline*. When the balance
reaches zero the instance is held at the baseline, so a service that needs
60% CPU suddenly gets 30%. The CPU graph then goes *down*, so an alert on
"CPU above 80%" never fires while latency explodes.

This module simulates the credit balance minute by minute, predicts when
it will reach zero from the recent burn rate, and alerts a configurable
time before that happens.
"""
from __future__ import annotations

from dataclasses import dataclass

MINUTES_PER_HOUR = 60
FULL_CPU_PERCENT = 100.0
NAIVE_CPU_ALERT_PERCENT = 80.0
DEFAULT_WARNING_MINUTES = 120
# Below this balance the instance is effectively pinned at its baseline.
EXHAUSTED_BALANCE = 1.0


@dataclass(frozen=True)
class BurstableSpec:
    """Credit rules for one instance size. One credit = one vCPU at 100% for one minute."""
    name: str
    vcpus: int
    baseline_percent: float      # per vCPU
    credits_per_hour: float
    max_balance: float


# Published AWS figures for t3.large.
T3_LARGE = BurstableSpec("t3.large", vcpus=2, baseline_percent=30.0,
                         credits_per_hour=36.0, max_balance=864.0)


@dataclass
class Minute:
    """What happened to the instance in one simulated minute."""
    demanded_percent: float
    delivered_percent: float
    balance_after: float

    @property
    def throttled(self) -> bool:
        return self.delivered_percent < self.demanded_percent


def naive_cpu_alert(delivered_percent: list[float],
                    threshold: float = NAIVE_CPU_ALERT_PERCENT) -> bool:
    """Original alert: fire only if measured CPU went above a fixed threshold."""
    for value in delivered_percent:
        if value > threshold:
            return True
    return False


def credits_needed(spec: BurstableSpec, cpu_percent: float) -> float:
    """Credits consumed in one minute at the given CPU use (percent of all vCPUs)."""
    return spec.vcpus * cpu_percent / FULL_CPU_PERCENT


def credits_earned_per_minute(spec: BurstableSpec) -> float:
    return spec.credits_per_hour / MINUTES_PER_HOUR


def simulate_minute(spec: BurstableSpec, balance: float, demanded_percent: float) -> Minute:
    """Spend credits for one minute; throttle when the balance cannot cover demand."""
    available = balance + credits_earned_per_minute(spec)
    needed = credits_needed(spec, demanded_percent)
    if needed <= available:
        # Unused credits accrue, but only up to the instance's cap.
        new_balance = min(spec.max_balance, available - needed)
        return Minute(demanded_percent, demanded_percent, new_balance)
    # Out of credits: the instance can only use what it earns plus what is left.
    delivered = available / spec.vcpus * FULL_CPU_PERCENT
    return Minute(demanded_percent, delivered, 0.0)


def simulate(spec: BurstableSpec, start_balance: float,
             demand_percent: list[float]) -> list[Minute]:
    """Run ``simulate_minute`` over a per-minute demand series."""
    minutes = []
    balance = start_balance
    for demanded in demand_percent:
        minute = simulate_minute(spec, balance, demanded)
        minutes.append(minute)
        balance = minute.balance_after
    return minutes


def minutes_until_exhaustion(spec: BurstableSpec, balance: float,
                             recent_percent: list[float]) -> float | None:
    """Predict minutes until the balance hits zero at the recent average CPU use.

    Returns None when the instance is earning at least as fast as it spends.
    """
    if not recent_percent:
        return None
    average = sum(recent_percent) / len(recent_percent)
    net_burn = credits_needed(spec, average) - credits_earned_per_minute(spec)
    if net_burn <= 0:
        return None
    return balance / net_burn


def should_warn(spec: BurstableSpec, balance: float, recent_percent: list[float],
                warning_minutes: float = DEFAULT_WARNING_MINUTES) -> bool:
    """Fire when credits will run out within ``warning_minutes`` (or already have)."""
    # Once throttled, measured CPU sits at the baseline, so the burn rate looks
    # like zero. Keep firing on the balance itself or the alert would resolve.
    if balance < EXHAUSTED_BALANCE:
        return True
    remaining = minutes_until_exhaustion(spec, balance, recent_percent)
    if remaining is None:
        return False
    return remaining <= warning_minutes


def first_warning_minute(spec: BurstableSpec, minutes: list[Minute], window: int,
                         warning_minutes: float = DEFAULT_WARNING_MINUTES) -> int | None:
    """Replay a simulation and return the first minute index at which the alert fires."""
    for index, minute in enumerate(minutes):
        start = max(0, index - window + 1)
        recent = [entry.delivered_percent for entry in minutes[start:index + 1]]
        if should_warn(spec, minute.balance_after, recent, warning_minutes):
            return index
    return None

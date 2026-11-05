"""Size rolling-deployment batches from live traffic, not a fixed percentage.

The deploy tool replaced 25% of the fleet at a time ("maxUnavailable: 25%").
That was safe at night, but a release started during the evening peak took a
quarter of the instances out while traffic needed more than three quarters of
them, so the remaining instances overloaded and checkout returned 503s.
This module computes each batch from current traffic and per-instance
capacity, waits when there is no spare capacity, and stops the rollout if
newly started instances fail their health check.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

# Never plan to run instances hotter than this; the rest is headroom for spikes.
TARGET_UTILIZATION = 0.8
NAIVE_MAX_UNAVAILABLE_FRACTION = 0.25
# How many consecutive "no spare capacity" checks before giving up.
MAX_WAIT_STEPS = 6

OLD_VERSION = "v1"
NEW_VERSION = "v2"


@dataclass
class Instance:
    """One server in the fleet."""
    name: str
    version: str


@dataclass
class RolloutStep:
    """What happened at one step of the rollout (one traffic reading)."""
    traffic_rps: float
    taken_down: int
    serving_capacity_rps: float
    overloaded: bool


@dataclass
class RolloutResult:
    """Outcome of a whole rollout."""
    status: str                       # "completed", "halted_unhealthy", "aborted_no_capacity"
    steps: list[RolloutStep] = field(default_factory=list)

    @property
    def overloaded_steps(self) -> int:
        """Number of steps where traffic exceeded what the serving fleet could handle."""
        return sum(1 for step in self.steps if step.overloaded)


def naive_batch_size(fleet_size: int) -> int:
    """Original rule: always replace a fixed 25% of the fleet, whatever the load."""
    return max(1, math.floor(fleet_size * NAIVE_MAX_UNAVAILABLE_FRACTION))


def instances_needed(traffic_rps: float, per_instance_rps: float) -> int:
    """Instances that must keep serving so none runs above the target utilization."""
    usable_rps_per_instance = per_instance_rps * TARGET_UTILIZATION
    return math.ceil(traffic_rps / usable_rps_per_instance)


def safe_batch_size(serving: int, traffic_rps: float, per_instance_rps: float,
                    max_batch: int) -> int:
    """Largest batch we can take out of service while the rest still carries traffic.

    Returns 0 when there is no spare capacity: the caller must wait instead.
    """
    spare_instances = serving - instances_needed(traffic_rps, per_instance_rps)
    if spare_instances <= 0:
        return 0
    return min(max_batch, spare_instances)


def make_fleet(size: int) -> list[Instance]:
    """A fleet where every instance runs the old version."""
    return [Instance(f"web-{index:02d}", OLD_VERSION) for index in range(size)]


def record_step(traffic_rps: float, taken_down: int, serving: int,
                per_instance_rps: float) -> RolloutStep:
    """Capture capacity while a batch is out of service (the risky moment)."""
    capacity = serving * per_instance_rps
    return RolloutStep(traffic_rps, taken_down, capacity, traffic_rps > capacity)


def replace_batch(fleet: list[Instance], batch_size: int,
                  is_healthy: Callable[[Instance], bool]) -> bool:
    """Upgrade up to ``batch_size`` old instances; return False if any new one is unhealthy."""
    old_instances = [instance for instance in fleet if instance.version == OLD_VERSION]
    for instance in old_instances[:batch_size]:
        instance.version = NEW_VERSION
        # Health gate: a bad build must not be rolled any further.
        if not is_healthy(instance):
            return False
    return True


def remaining_old(fleet: list[Instance]) -> int:
    """How many instances still run the old version."""
    return sum(1 for instance in fleet if instance.version == OLD_VERSION)


def run_rollout(fleet: list[Instance], traffic_by_step: list[float], per_instance_rps: float,
                batch_planner: Callable[[int, float], int],
                is_healthy: Callable[[Instance], bool]) -> RolloutResult:
    """Roll the fleet to the new version, one traffic reading per step.

    ``batch_planner(serving, traffic_rps)`` decides how many instances to
    replace at this step; 0 means "wait for traffic to drop".
    """
    result = RolloutResult(status="aborted_no_capacity")
    waits_in_a_row = 0
    for traffic_rps in traffic_by_step:
        if remaining_old(fleet) == 0:
            break
        batch = min(batch_planner(len(fleet), traffic_rps), remaining_old(fleet))
        if batch == 0:
            waits_in_a_row += 1
            if waits_in_a_row > MAX_WAIT_STEPS:
                return result
            continue
        waits_in_a_row = 0
        result.steps.append(record_step(traffic_rps, batch, len(fleet) - batch, per_instance_rps))
        if not replace_batch(fleet, batch, is_healthy):
            result.status = "halted_unhealthy"
            return result
    result.status = "completed" if remaining_old(fleet) == 0 else "aborted_no_capacity"
    return result


def naive_planner() -> Callable[[int, float], int]:
    """Batch planner for the original fixed-percentage rollout."""
    def plan(fleet_size: int, traffic_rps: float) -> int:
        return naive_batch_size(fleet_size)
    return plan


def capacity_aware_planner(per_instance_rps: float, max_batch: int) -> Callable[[int, float], int]:
    """Batch planner that only takes out instances the current traffic does not need."""
    def plan(fleet_size: int, traffic_rps: float) -> int:
        return safe_batch_size(fleet_size, traffic_rps, per_instance_rps, max_batch)
    return plan

"""Autoscale queue workers on backlog, not on CPU.

Email-sending workers spend almost all their time waiting on an external
API, so even when every worker is busy their CPU sits around 15%. The
autoscaler targeted 60% CPU, decided the workers were idle and scaled them
DOWN while the queue backlog grew for hours.

This module simulates the queue minute by minute with two scalers:
``naive_cpu_scaler`` (the original, utilisation-based) and
``backlog_scaler``, which sizes the fleet from the work that is actually
waiting: enough workers to keep up with arrivals plus enough to drain the
current backlog within a target time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

# Each worker sends 5 emails a second when it is never idle.
MESSAGES_PER_WORKER_PER_MINUTE = 300
# CPU used by a worker that is 100% busy: it is mostly waiting on network I/O.
CPU_WHEN_FULLY_BUSY = 0.15
TARGET_CPU = 0.60
# The backlog scaler aims to clear any backlog within this many minutes.
TARGET_DRAIN_MINUTES = 10


@dataclass
class ScalerLimits:
    """Bounds every scaler must respect."""
    min_workers: int = 2
    max_workers: int = 40

    def clamp(self, workers: int) -> int:
        """Keep a desired worker count inside [min_workers, max_workers]."""
        return max(self.min_workers, min(self.max_workers, workers))


@dataclass
class QueueMetrics:
    """What a scaler can observe at the end of a minute."""
    workers: int
    backlog: int              # messages still waiting in the queue
    arrivals_last_minute: int
    cpu_utilization: float    # average per worker, 0.0 - 1.0


def naive_cpu_scaler(metrics: QueueMetrics, limits: ScalerLimits) -> int:
    """Original scaler: the HPA formula desired = current * cpu / target.

    It is correct for CPU-bound work. For I/O-bound workers the CPU never
    reaches the target even at full load, so it always recommends fewer pods.
    """
    desired = math.ceil(metrics.workers * metrics.cpu_utilization / TARGET_CPU)
    return limits.clamp(desired)


def backlog_scaler(metrics: QueueMetrics, limits: ScalerLimits) -> int:
    """Fixed scaler: size the fleet from arrivals and waiting messages.

    Workers needed to keep up with new messages, plus workers needed to empty
    the current backlog within TARGET_DRAIN_MINUTES. Neither term depends on
    how much CPU the work happens to use.
    """
    if metrics.backlog < 0 or metrics.arrivals_last_minute < 0:
        raise ValueError("backlog and arrivals must be non-negative")
    keep_up = metrics.arrivals_last_minute / MESSAGES_PER_WORKER_PER_MINUTE
    drain_capacity = MESSAGES_PER_WORKER_PER_MINUTE * TARGET_DRAIN_MINUTES
    drain = metrics.backlog / drain_capacity
    return limits.clamp(math.ceil(keep_up + drain))


# A scaler turns this minute's metrics into next minute's worker count.
Scaler = Callable[[QueueMetrics, ScalerLimits], int]


@dataclass
class SimResult:
    """Per-minute history of one simulation run."""
    workers: list[int] = field(default_factory=list)
    backlog: list[int] = field(default_factory=list)

    def max_backlog(self) -> int:
        """Largest number of messages waiting at the end of any minute."""
        return max(self.backlog)

    def max_wait_minutes(self) -> float:
        """Worst queueing delay: backlog divided by that minute's capacity."""
        worst = 0.0
        for workers, backlog in zip(self.workers, self.backlog):
            capacity = workers * MESSAGES_PER_WORKER_PER_MINUTE
            worst = max(worst, backlog / capacity)
        return worst


def process_one_minute(workers: int, backlog: int, arrivals: int) -> tuple[int, float]:
    """Return (new backlog, average CPU per worker) after one minute of work."""
    capacity = workers * MESSAGES_PER_WORKER_PER_MINUTE
    available = backlog + arrivals
    processed = min(capacity, available)
    busy_fraction = processed / capacity
    return available - processed, busy_fraction * CPU_WHEN_FULLY_BUSY


def simulate(arrivals_per_minute: list[int], scaler: Scaler, limits: ScalerLimits,
             start_workers: int) -> SimResult:
    """Run the queue for len(arrivals_per_minute) minutes.

    The scaler's decision at the end of minute N takes effect in minute N+1,
    like a real autoscaler with a one-minute evaluation loop.
    """
    result = SimResult()
    workers = start_workers
    backlog = 0
    for arrivals in arrivals_per_minute:
        backlog, cpu = process_one_minute(workers, backlog, arrivals)
        result.workers.append(workers)
        result.backlog.append(backlog)
        metrics = QueueMetrics(workers, backlog, arrivals, cpu)
        workers = scaler(metrics, limits)
    return result


def campaign_traffic(normal: int = 1_000, peak: int = 3_000, peak_start: int = 60,
                     peak_minutes: int = 120, total_minutes: int = 360) -> list[int]:
    """Arrivals per minute: steady traffic with a marketing-campaign peak."""
    traffic = []
    for minute in range(total_minutes):
        in_peak = peak_start <= minute < peak_start + peak_minutes
        traffic.append(peak if in_peak else normal)
    return traffic

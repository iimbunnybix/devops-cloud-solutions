"""Stop a CPU-based autoscaler from flapping.

The original bug: a custom autoscaler for the search API recomputed the
desired replica count every 15 s as ``ceil(cpu_demand / target)`` and applied
it immediately in both directions. Normal second-to-second noise in traffic
made it add pods, remove them a tick later, and add them back. Every removal
killed pods with warm caches, and every new pod served its first requests cold,
so p99 latency spiked all day while the replica count sawtoothed.

Fix (the same rules the Kubernetes HorizontalPodAutoscaler uses):
* a **tolerance band**: ignore ratios within ±10% of the target;
* **scale up fast**, but at most +100% or +4 pods per step, whichever is more;
* **scale down slowly**: use the *highest* recommendation from the last
  5 minutes (the downscale stabilization window), so a brief dip does not
  remove capacity that will be needed again seconds later;
* clamp to ``[min_replicas, max_replicas]``.
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass


@dataclass
class ScalerConfig:
    target_utilization: float = 0.6
    tolerance: float = 0.1                 # HPA default
    scale_down_window_s: float = 300.0     # HPA default downscale stabilization
    scale_up_max_percent: float = 100.0    # HPA default policy: 100% ...
    scale_up_max_pods: int = 4             # ... or 4 pods per period, whichever is larger
    min_replicas: int = 2
    max_replicas: int = 50


def raw_recommendation(current: int, cpu_demand: float, cfg: ScalerConfig) -> int:
    """Replicas needed so that per-pod utilization equals the target (1 core per pod)."""
    if current <= 0:
        raise ValueError("current replicas must be positive")
    utilization = cpu_demand / current
    return math.ceil(current * utilization / cfg.target_utilization)


class NaiveScaler:
    """The buggy version: apply the raw recommendation every tick."""

    def __init__(self, cfg: ScalerConfig, replicas: int):
        self.cfg, self.replicas = cfg, replicas

    def step(self, now: float, cpu_demand: float) -> int:
        desired = raw_recommendation(self.replicas, cpu_demand, self.cfg)
        self.replicas = min(self.cfg.max_replicas, max(self.cfg.min_replicas, desired))
        return self.replicas


class StabilizedScaler:
    def __init__(self, cfg: ScalerConfig, replicas: int):
        self.cfg, self.replicas = cfg, replicas
        self._history: deque[tuple[float, int]] = deque()   # (time, recommendation)

    def step(self, now: float, cpu_demand: float) -> int:
        cfg, current = self.cfg, self.replicas
        ratio = (cpu_demand / current) / cfg.target_utilization
        if abs(ratio - 1.0) <= cfg.tolerance:
            rec = current                                   # inside the band: no change
        else:
            rec = math.ceil(current * ratio)
        rec = min(cfg.max_replicas, max(cfg.min_replicas, rec))

        if not self._history:
            # First evaluation after (re)start: count the replicas we started
            # with as a recommendation, so a dip right after a restart is held
            # for the full window like any other dip.
            self._history.append((now, current))
        self._history.append((now, rec))
        while self._history and self._history[0][0] <= now - cfg.scale_down_window_s:
            self._history.popleft()

        if rec > current:
            limit = max(math.ceil(current * (1 + cfg.scale_up_max_percent / 100)),
                        current + cfg.scale_up_max_pods)
            new = min(rec, limit)
        else:
            # Highest recommendation in the window: only shrink once demand has
            # stayed low for the whole window.
            new = min(current, max(r for _, r in self._history))
        self.replicas = min(cfg.max_replicas, max(cfg.min_replicas, new))
        return self.replicas


def noisy_load(seconds: int, step_s: int, base: float, noise: float, seed: int) -> list[float]:
    """CPU demand in cores: a flat base with uniform ±noise jitter each tick."""
    rng = random.Random(seed)
    return [base * (1 + rng.uniform(-noise, noise)) for _ in range(0, seconds, step_s)]


def simulate(scaler, demand: list[float], step_s: int) -> dict:
    """Run a scaler over a demand series and summarize its behaviour."""
    replicas, changes, overloaded = [], 0, 0
    prev = scaler.replicas
    for i, d in enumerate(demand):
        r = scaler.step(i * step_s, d)
        changes += r != prev
        prev = r
        replicas.append(r)
        overloaded += d / r > 0.9          # pods above 90% CPU: latency degrades
    return {"changes": changes, "replicas": replicas, "overloaded_ticks": overloaded,
            "pod_starts": sum(max(0, b - a) for a, b in zip([replicas[0]] + replicas, replicas))}

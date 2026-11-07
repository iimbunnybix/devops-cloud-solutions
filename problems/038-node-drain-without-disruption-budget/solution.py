"""Drain nodes for an upgrade without taking a service to zero replicas.

A cluster upgrade drained two nodes at a time and deleted every pod on them
at once. All three replicas of the login service happened to sit on those
two nodes, so login had no ready pod until replacements started a minute
later, and it happened again on the next batch.

This module simulates a drain in 5-second ticks. ``naive_eviction_allowed``
lets every eviction through (no PodDisruptionBudget). ``budget_eviction_allowed``
behaves like the Kubernetes Eviction API with a PDB: an eviction is refused
when it would leave a service with fewer ready pods than ``min_available``,
and the drainer retries until a replacement is ready. ``check_budgets``
flags budgets that can never allow an eviction, and the drain gives up
with a clear "blocked" result instead of hanging forever.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

TICK_SECONDS = 5
POD_STARTUP_SECONDS = 60      # image pull + app start + readiness probe
NODE_UPGRADE_SECONDS = 120    # reboot into the new kubelet version
DRAIN_TIMEOUT_SECONDS = 1_800


@dataclass
class Pod:
    service: str
    node: str
    ready_at: int = 0          # time when the readiness probe first passes

    def is_ready(self, now: int) -> bool:
        """A pod serves traffic only once it has started and passed readiness."""
        return now >= self.ready_at


@dataclass
class Cluster:
    nodes: list[str]
    pods: list[Pod]
    cordoned: set[str] = field(default_factory=set)

    def ready_count(self, service: str, now: int) -> int:
        """Number of pods of ``service`` that are serving right now."""
        count = 0
        for pod in self.pods:
            if pod.service == service and pod.is_ready(now):
                count += 1
        return count

    def pods_on(self, nodes: list[str]) -> list[Pod]:
        """Pods currently scheduled on any of ``nodes``."""
        return [pod for pod in self.pods if pod.node in nodes]

    def least_loaded_schedulable_node(self) -> str:
        """Where the scheduler would put a replacement pod."""
        best_node, best_load = "", -1
        for node in self.nodes:
            if node in self.cordoned:
                continue
            load = len(self.pods_on([node]))
            if best_load == -1 or load < best_load:
                best_node, best_load = node, load
        if not best_node:
            raise RuntimeError("no schedulable node for replacement pods")
        return best_node


# Decides whether one pod may be evicted right now.
EvictionPolicy = Callable[[Cluster, Pod, int, dict[str, int]], bool]


def naive_eviction_allowed(cluster: Cluster, pod: Pod, now: int,
                           min_available: dict[str, int]) -> bool:
    """Original drain: no disruption budget, every eviction succeeds."""
    return True


def budget_eviction_allowed(cluster: Cluster, pod: Pod, now: int,
                            min_available: dict[str, int]) -> bool:
    """Eviction API with a PDB: keep at least min_available ready pods.

    Evicting a pod that is not ready yet costs nothing, so only ready pods
    count against the budget (Kubernetes does the same).
    """
    ready_after = cluster.ready_count(pod.service, now)
    if pod.is_ready(now):
        ready_after -= 1
    return ready_after >= min_available.get(pod.service, 0)


@dataclass
class DrainResult:
    completed: bool
    finished_at: int
    min_ready: dict[str, int]          # lowest ready count seen per service
    zero_ready_seconds: dict[str, int]  # time each service had no ready pod
    blocked_pods: list[str] = field(default_factory=list)


def evict(cluster: Cluster, pod: Pod, now: int) -> None:
    """Delete ``pod`` and let its controller create a replacement elsewhere."""
    cluster.pods.remove(pod)
    new_node = cluster.least_loaded_schedulable_node()
    cluster.pods.append(Pod(pod.service, new_node, now + POD_STARTUP_SECONDS))


def try_evictions(cluster: Cluster, batch: list[str], now: int,
                  policy: EvictionPolicy, min_available: dict[str, int]) -> None:
    """One pass of `kubectl drain`: attempt every pod still on the batch."""
    for pod in list(cluster.pods_on(batch)):
        if policy(cluster, pod, now, min_available):
            evict(cluster, pod, now)


@dataclass
class ReadyStats:
    """Running availability statistics collected once per tick."""
    min_ready: dict[str, int]
    zero_ready_seconds: dict[str, int]


def record_ready(cluster: Cluster, now: int, stats: ReadyStats) -> None:
    """Track each service's lowest ready count and time spent at zero."""
    for service in stats.min_ready:
        ready = cluster.ready_count(service, now)
        stats.min_ready[service] = min(stats.min_ready[service], ready)
        if ready == 0:
            stats.zero_ready_seconds[service] += TICK_SECONDS


def wait_for_upgrade(cluster: Cluster, now: int, stats: ReadyStats) -> int:
    """Nodes reboot into the new version; pods elsewhere keep starting."""
    upgrade_done = now + NODE_UPGRADE_SECONDS
    while now < upgrade_done:
        now += TICK_SECONDS
        record_ready(cluster, now, stats)
    return now


def drain_cluster(cluster: Cluster, batches: list[list[str]], policy: EvictionPolicy,
                  min_available: dict[str, int]) -> DrainResult:
    """Drain and upgrade each batch of nodes in order.

    A batch is finished only when no pod is left on it. If evictions keep
    being refused for DRAIN_TIMEOUT_SECONDS the drain stops and reports the
    pods it could not move, instead of hanging or force-deleting them.
    """
    services = {pod.service for pod in cluster.pods}
    stats = ReadyStats({service: cluster.ready_count(service, 0) for service in services},
                       {service: 0 for service in services})
    now = 0
    for batch in batches:
        cluster.cordoned.update(batch)
        batch_started = now
        while cluster.pods_on(batch):
            if now - batch_started >= DRAIN_TIMEOUT_SECONDS:
                stuck = [f"{pod.service}@{pod.node}" for pod in cluster.pods_on(batch)]
                return DrainResult(False, now, stats.min_ready,
                                   stats.zero_ready_seconds, stuck)
            try_evictions(cluster, batch, now, policy, min_available)
            record_ready(cluster, now, stats)
            now += TICK_SECONDS
        now = wait_for_upgrade(cluster, now, stats)
        cluster.cordoned.difference_update(batch)
    return DrainResult(True, now, stats.min_ready, stats.zero_ready_seconds)


def check_budgets(replicas: dict[str, int], min_available: dict[str, int]) -> list[str]:
    """Lint disruption budgets before an upgrade starts.

    A service with no budget can lose every replica in one drain. A budget
    of min_available >= replicas can never allow an eviction, so the drain
    would block until it times out.
    """
    problems = []
    for service in sorted(replicas):
        count = replicas[service]
        if service not in min_available:
            problems.append(f"{service}: no disruption budget; a drain may evict all "
                            f"{count} replicas at once")
        elif min_available[service] >= count:
            problems.append(f"{service}: min_available={min_available[service]} with "
                            f"{count} replicas blocks every eviction")
    return problems


def example_cluster() -> Cluster:
    """Four nodes; the scheduler happened to put all of login on n1 and n2."""
    pods = [Pod("login", "n1"), Pod("login", "n1"), Pod("login", "n2"),
            Pod("checkout", "n3"), Pod("checkout", "n4"), Pod("search", "n2"),
            Pod("search", "n3")]
    return Cluster(["n1", "n2", "n3", "n4"], pods)


UPGRADE_BATCHES = [["n1", "n2"], ["n3", "n4"]]
BUDGETS = {"login": 2, "checkout": 1, "search": 1}

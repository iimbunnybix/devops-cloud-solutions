"""Spread replicas across availability zones so one zone outage cannot take a service down.

The cluster scheduler scored nodes "most allocated first" to pack pods tightly
and save money. With nothing else to tell it apart, it placed all six replicas
of the login service on nodes in one availability zone, and when that zone
lost power the whole service went down. This module keeps the bin-packing
choice inside a zone, but first picks the zone that keeps the replica counts
per zone within ``max_skew`` of each other (like Kubernetes
``topologySpreadConstraints`` with ``whenUnsatisfiable: DoNotSchedule``), and
offers a check that a placement survives the loss of any single zone.
"""
from __future__ import annotations

from dataclasses import dataclass

# Allowed difference between the most and least loaded zone.
DEFAULT_MAX_SKEW = 1


@dataclass
class Node:
    """A worker node with the CPU (millicores) still free for new pods."""
    name: str
    zone: str
    free_cpu_millis: int


class UnschedulableError(Exception):
    """Raised when a replica cannot be placed without breaking the spread rule."""


def _tightest_fitting_node(nodes: list[Node], cpu_millis: int) -> Node | None:
    """Bin-packing choice: the node with the least free CPU that still fits."""
    best: Node | None = None
    for node in nodes:
        if node.free_cpu_millis < cpu_millis:
            continue
        if best is None or node.free_cpu_millis < best.free_cpu_millis:
            best = node
    return best


def naive_bin_pack(nodes: list[Node], replicas: int, cpu_millis: int) -> dict[str, str]:
    """The original scheduler: pack each replica onto the fullest node that fits."""
    placement: dict[str, str] = {}
    for replica_index in range(replicas):
        node = _tightest_fitting_node(nodes, cpu_millis)
        if node is None:
            raise UnschedulableError(f"replica-{replica_index}: no node has room")
        node.free_cpu_millis -= cpu_millis
        placement[f"replica-{replica_index}"] = node.name
    return placement


def _zones(nodes: list[Node]) -> list[str]:
    """All zones that have at least one node, in a stable order."""
    return sorted({node.zone for node in nodes})


def _zone_order_by_count(zone_counts: dict[str, int]) -> list[str]:
    """Zones with the fewest replicas first; ties broken by name for determinism."""
    return sorted(zone_counts, key=lambda zone: (zone_counts[zone], zone))


def _skew_after_adding(zone_counts: dict[str, int], zone: str) -> int:
    """The max-minus-min replica count if one more replica goes into ``zone``."""
    counts_after = dict(zone_counts)
    counts_after[zone] += 1
    return max(counts_after.values()) - min(counts_after.values())


def spread_place(nodes: list[Node], replicas: int, cpu_millis: int,
                 max_skew: int = DEFAULT_MAX_SKEW) -> dict[str, str]:
    """Place replicas zone by zone, never letting per-zone counts differ by more than max_skew.

    A zone that is full still counts with its current replicas, so a full zone
    blocks the others from running ahead instead of being silently ignored;
    that is what makes the guarantee hold.
    """
    if max_skew < 1:
        raise ValueError("max_skew must be at least 1")
    zone_counts = {zone: 0 for zone in _zones(nodes)}
    placement: dict[str, str] = {}
    for replica_index in range(replicas):
        node = _choose_node(nodes, zone_counts, cpu_millis, max_skew)
        if node is None:
            raise UnschedulableError(
                f"replica-{replica_index}: no zone can take it within max_skew={max_skew} "
                f"(current per-zone counts: {zone_counts})")
        node.free_cpu_millis -= cpu_millis
        zone_counts[node.zone] += 1
        placement[f"replica-{replica_index}"] = node.name
    return placement


def _choose_node(nodes: list[Node], zone_counts: dict[str, int], cpu_millis: int,
                 max_skew: int) -> Node | None:
    """Least-loaded allowed zone first, then the tightest-fitting node inside it."""
    for zone in _zone_order_by_count(zone_counts):
        if _skew_after_adding(zone_counts, zone) > max_skew:
            continue
        zone_nodes = [node for node in nodes if node.zone == zone]
        node = _tightest_fitting_node(zone_nodes, cpu_millis)
        if node is not None:
            return node
    return None


def replicas_left_after_zone_loss(placement: dict[str, str], nodes: list[Node]) -> dict[str, int]:
    """For each zone, how many replicas keep running if that zone goes down."""
    zone_of_node = {node.name: node.zone for node in nodes}
    left: dict[str, int] = {}
    for lost_zone in _zones(nodes):
        surviving = 0
        for node_name in placement.values():
            if zone_of_node[node_name] != lost_zone:
                surviving += 1
        left[lost_zone] = surviving
    return left


def survives_any_zone_loss(placement: dict[str, str], nodes: list[Node],
                           min_available: int) -> bool:
    """True if losing any single zone still leaves at least min_available replicas."""
    for surviving in replicas_left_after_zone_loss(placement, nodes).values():
        if surviving < min_available:
            return False
    return True

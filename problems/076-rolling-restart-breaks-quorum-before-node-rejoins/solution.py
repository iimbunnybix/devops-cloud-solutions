"""A rolling restart took down a 3-node quorum cluster that should survive it.

The restart script moved to the next node as soon as the previous node's
client port was open. But an etcd/ZooKeeper-style node with an open port is
still replaying the log it missed and is not yet a voting member. The script
restarted node 2 while node 1 was catching up, leaving one voter out of three:
no majority, so every write failed. The fix gates each step on cluster
health: the restarted node must report in sync, and the cluster must have
enough in-sync voters that taking the next node down still leaves a majority.
If that does not happen within a timeout, the rollout aborts instead of
pressing on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

PROCESS_START_SECONDS = 5       # restart until the client port accepts connections
CATCH_UP_SECONDS = 30           # port open until the node is an in-sync voter
POLL_INTERVAL_SECONDS = 1
STEP_TIMEOUT_SECONDS = 120      # give up on a step after this long


class FakeClock:
    """Simulated time, so tests are instant and deterministic."""

    def __init__(self) -> None:
        self.now = 0

    def advance(self, seconds: int) -> None:
        """Move simulated time forward."""
        self.now += seconds


@dataclass
class FakeNode:
    """A cluster member. ``catch_up_seconds=None`` means it never catches up."""

    name: str
    catch_up_seconds: int | None = CATCH_UP_SECONDS
    restarted_at: int | None = None  # None: running and in sync since before the test
    down: bool = False               # crashed and not coming back on its own

    def restart(self, now: int) -> None:
        """Stop and start the process; it must start and catch up again."""
        self.restarted_at = now
        self.down = False

    def port_open(self, now: int) -> bool:
        """What the original script checked: is the process accepting connections?"""
        if self.down:
            return False
        if self.restarted_at is None:
            return True
        return now >= self.restarted_at + PROCESS_START_SECONDS

    def in_sync(self, now: int) -> bool:
        """Is this node a caught-up voting member (what quorum really needs)?"""
        if not self.port_open(now):
            return False
        if self.restarted_at is None:
            return True
        if self.catch_up_seconds is None:
            return False
        caught_up_at = self.restarted_at + PROCESS_START_SECONDS + self.catch_up_seconds
        return now >= caught_up_at


@dataclass
class FakeCluster:
    """Nodes plus a clock; records every second the cluster had no quorum."""

    nodes: list[FakeNode]
    clock: FakeClock = field(default_factory=FakeClock)
    seconds_without_quorum: int = 0

    def majority(self) -> int:
        """Voters needed for quorum: 2 of 3, 3 of 5."""
        return len(self.nodes) // 2 + 1

    def in_sync_count(self, excluding: FakeNode | None = None) -> int:
        """How many nodes are in-sync voters right now, optionally ignoring one."""
        count = 0
        for node in self.nodes:
            if node is not excluding and node.in_sync(self.clock.now):
                count += 1
        return count

    def has_quorum(self) -> bool:
        """Writes succeed only while a majority of voters is in sync."""
        return self.in_sync_count() >= self.majority()

    def tick(self) -> None:
        """Advance one poll interval, noting whether writes would have failed."""
        if not self.has_quorum():
            self.seconds_without_quorum += POLL_INTERVAL_SECONDS
        self.clock.advance(POLL_INTERVAL_SECONDS)


def wait_until(cluster: FakeCluster, condition: Callable[[], bool], timeout: int) -> bool:
    """Poll ``condition`` until it is true or ``timeout`` seconds pass."""
    deadline = cluster.clock.now + timeout
    while not condition():
        if cluster.clock.now >= deadline:
            return False
        cluster.tick()
    return True


@dataclass
class RolloutResult:
    restarted: list[str]
    aborted_reason: str | None = None


# ---------------------------------------------------------------- original
def naive_rolling_restart(cluster: FakeCluster) -> RolloutResult:
    """Original script: restart a node, wait for its port, move on."""
    result = RolloutResult(restarted=[])
    for node in cluster.nodes:
        node.restart(cluster.clock.now)
        result.restarted.append(node.name)
        wait_until(cluster, lambda: node.port_open(cluster.clock.now), STEP_TIMEOUT_SECONDS)
    return result


# ---------------------------------------------------------------- the fix
def safe_to_restart(cluster: FakeCluster, node: FakeNode) -> bool:
    """True if the cluster keeps a majority even with ``node`` down."""
    return cluster.in_sync_count(excluding=node) >= cluster.majority()


def health_gated_rolling_restart(cluster: FakeCluster,
                                 timeout: int = STEP_TIMEOUT_SECONDS) -> RolloutResult:
    """Restart one node at a time, only when the cluster can afford it.

    Before each restart: wait until the other nodes alone form a majority.
    After each restart: wait until the node is back as an in-sync voter.
    Either wait timing out aborts the rollout, leaving the remaining nodes
    untouched; continuing would be what turns a slow node into an outage.
    """
    result = RolloutResult(restarted=[])
    for node in cluster.nodes:
        if not wait_until(cluster, lambda: safe_to_restart(cluster, node), timeout):
            result.aborted_reason = (f"not restarting {node.name}: only "
                                     f"{cluster.in_sync_count(excluding=node)} other in-sync "
                                     f"voters, need {cluster.majority()}")
            return result
        node.restart(cluster.clock.now)
        result.restarted.append(node.name)
        if not wait_until(cluster, lambda: node.in_sync(cluster.clock.now), timeout):
            result.aborted_reason = f"{node.name} not in sync after {timeout}s"
            return result
    return result


def make_cluster(size: int) -> FakeCluster:
    """A healthy cluster of ``size`` nodes named node-1, node-2, ..."""
    nodes = []
    for number in range(1, size + 1):
        nodes.append(FakeNode(name=f"node-{number}"))
    return FakeCluster(nodes=nodes)

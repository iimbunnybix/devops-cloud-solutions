"""Decide that a rolling deployment is finished only when the NEW version is serving.

The original bug: the deploy script waited until "ready pods with label
app=checkout >= replicas". Old-revision pods carry the same label and stay
ready during a rolling update, so the check passed at once, even when every
new pod was crash-looping. The pipeline reported success and moved on.

Fix: count only pods of the target revision (the pod-template hash), require
every old-revision pod to be gone, fail fast when a new pod keeps
restarting, and give up after a progress deadline instead of waiting forever.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

CHECK_INTERVAL_SECONDS = 10
PROGRESS_DEADLINE_SECONDS = 600        # Kubernetes' default progressDeadlineSeconds
STARTUP_SECONDS = 30                   # a healthy pod becomes ready after this
RESTART_EVERY_SECONDS = 20             # a crashing pod restarts this often (simplified backoff)
MAX_RESTARTS = 3                       # more than this is CrashLoopBackOff, not slow startup

# How the new version behaves once started.
HEALTHY, CRASHING, NEVER_READY = "healthy", "crashing", "never ready"   # never ready: readiness probe fails
COMPLETE, IN_PROGRESS, FAILED, TIMED_OUT = "complete", "in progress", "failed", "timed out"


@dataclass
class Pod:
    revision: str
    created_at: int
    ready: bool = False
    restarts: int = 0
    labels: dict[str, str] = field(default_factory=lambda: {"app": "checkout"})


@dataclass
class RollingUpdate:
    """A tiny Deployment controller doing a rolling update with maxSurge=1, maxUnavailable=0."""

    replicas: int
    new_revision: str
    new_version_behaviour: str
    pods: list[Pod] = field(default_factory=list)

    def tick(self, now: int) -> None:
        """Advance the controller to time ``now``: update pods, then surge or scale down."""
        for pod in self.new_pods():
            self.update_new_pod(pod, now)
        self.scale_down_one_old_pod_if_safe()
        if len(self.new_pods()) < self.replicas and len(self.pods) < self.replicas + 1:
            self.pods.append(Pod(self.new_revision, created_at=now))

    def update_new_pod(self, pod: Pod, now: int) -> None:
        """Make a new pod ready, restart it, or leave it unready, by the version's behaviour."""
        age = now - pod.created_at
        if self.new_version_behaviour == HEALTHY:
            pod.ready = age >= STARTUP_SECONDS
        elif self.new_version_behaviour == CRASHING:
            pod.restarts = age // RESTART_EVERY_SECONDS

    def scale_down_one_old_pod_if_safe(self) -> None:
        # WHY: maxUnavailable=0 means an old pod may go only once ready pods exceed replicas.
        ready_count = sum(1 for pod in self.pods if pod.ready)
        old = self.old_pods()
        if old and ready_count > self.replicas:
            self.pods.remove(old[0])

    def new_pods(self) -> list[Pod]:
        """Pods already running the revision being rolled out."""
        return [pod for pod in self.pods if pod.revision == self.new_revision]

    def old_pods(self) -> list[Pod]:
        """Pods still on any earlier revision."""
        return [pod for pod in self.pods if pod.revision != self.new_revision]


def start_rollout(replicas: int, old_revision: str, new_revision: str,
                  new_version_behaviour: str) -> RollingUpdate:
    """A Deployment fully on ``old_revision`` that has just been told to roll out a new one."""
    old_pods = [Pod(old_revision, created_at=0, ready=True) for _ in range(replicas)]
    return RollingUpdate(replicas, new_revision, new_version_behaviour, old_pods)


def naive_rollout_status(pods: list[Pod], replicas: int) -> str:
    """The original check: enough ready pods with the app label, whatever their version."""
    ready = 0
    for pod in pods:
        if pod.labels.get("app") == "checkout" and pod.ready:
            ready += 1
    return COMPLETE if ready >= replicas else IN_PROGRESS


def rollout_status(pods: list[Pod], replicas: int, target_revision: str) -> str:
    """Complete only when all replicas run the target revision and no old pod is left."""
    new_ready = 0
    old_left = 0
    for pod in pods:
        if pod.revision != target_revision:
            old_left += 1
            continue
        if pod.restarts > MAX_RESTARTS:
            # WHY: more waiting will not help a crash loop; fail now so rollback starts.
            return FAILED
        if pod.ready:
            new_ready += 1
    if new_ready >= replicas and old_left == 0:
        return COMPLETE
    return IN_PROGRESS


def wait_for_rollout(rollout: RollingUpdate, check: Callable[[list[Pod]], str],
                     deadline_seconds: int = PROGRESS_DEADLINE_SECONDS) -> tuple[str, int]:
    """Poll the rollout with ``check`` until it is final or the deadline passes.

    Returns the final status and the simulated second it was decided at.
    """
    now = 0
    while now <= deadline_seconds:
        rollout.tick(now)
        status = check(rollout.pods)
        if status != IN_PROGRESS:
            return status, now
        now += CHECK_INTERVAL_SECONDS
    return TIMED_OUT, now

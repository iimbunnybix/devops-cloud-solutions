"""Make every config or secret change trigger a rollout, and detect pods that missed it.

The payments API key was rotated in the config store, but the deployment spec
did not change, so the orchestrator saw nothing to do. Running pods kept the
key they had read at start-up; when the old key was revoked, every call failed.

The fix puts a checksum of the rendered config into the deployment spec's
annotations. Any config change changes the checksum, so the spec differs and
a normal rollout replaces the pods. A drift check compares the config hash
each running pod reports with the desired one and lists pods still on old config.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

CHECKSUM_ANNOTATION = "checksum/config"
SHORT_HASH_LENGTH = 12   # enough to tell versions apart in dashboards and logs


@dataclass(frozen=True)
class DeploymentSpec:
    """What the orchestrator compares to decide whether a rollout is needed."""

    image: str
    config_name: str
    replicas: int
    annotations: tuple[tuple[str, str], ...] = ()


@dataclass
class Pod:
    """A running instance. It reads its config once, when it starts."""

    name: str
    config_values: dict[str, str]
    config_hash: str


@dataclass
class Orchestrator:
    """A tiny model of a Deployment controller: it only acts when the spec changes."""

    config_store: dict[str, dict[str, str]]
    current_spec: DeploymentSpec | None = None
    pods: list[Pod] = field(default_factory=list)
    rollouts: int = 0

    def apply(self, spec: DeploymentSpec) -> bool:
        """Apply a spec; return True if it caused a rollout."""
        # Like Kubernetes, an identical spec is a no-op: nothing restarts,
        # even if the config the pods read has changed in the store.
        if spec == self.current_spec:
            return False
        self.current_spec = spec
        self.pods = self._start_pods(spec)
        self.rollouts += 1
        return True

    def _start_pods(self, spec: DeploymentSpec) -> list[Pod]:
        """Start fresh pods that each read the config store as it is now."""
        values = dict(self.config_store[spec.config_name])
        pods = []
        for index in range(spec.replicas):
            pod_name = f"{spec.config_name}-{self.rollouts + 1}-{index}"
            pods.append(Pod(pod_name, values, config_checksum(values)))
        return pods


def render_config(values: dict[str, str]) -> str:
    """Canonical text form of the config: same values always give the same text."""
    # Sorted keys, so reordering keys in the store does not look like a change.
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def config_checksum(values: dict[str, str]) -> str:
    """Short SHA-256 of the rendered config. Safe to show: it does not reveal secrets."""
    digest = hashlib.sha256(render_config(values).encode("utf-8")).hexdigest()
    return digest[:SHORT_HASH_LENGTH]


def naive_render_deployment(image: str, config_name: str, replicas: int) -> DeploymentSpec:
    """Original template: refers to the config by name only.

    The name never changes when the values do, so the spec never changes either.
    """
    return DeploymentSpec(image, config_name, replicas)


def render_deployment(image: str, config_name: str, replicas: int,
                      config_store: dict[str, dict[str, str]]) -> DeploymentSpec:
    """Fixed template: the spec carries a checksum of the config it depends on."""
    checksum = config_checksum(config_store[config_name])
    annotations = ((CHECKSUM_ANNOTATION, checksum),)
    return DeploymentSpec(image, config_name, replicas, annotations)


def stale_pods(pods: list[Pod], desired_hash: str) -> list[str]:
    """Names of pods running a config hash other than the desired one."""
    stale = []
    for pod in pods:
        if pod.config_hash != desired_hash:
            stale.append(pod.name)
    return stale


def drift_report(orchestrator: Orchestrator, config_name: str) -> dict[str, list[str]]:
    """Group running pods by the config hash they report, for an alert or dashboard.

    More than one key, or a key different from the store's hash, means drift.
    """
    by_hash: dict[str, list[str]] = {}
    for pod in orchestrator.pods:
        by_hash.setdefault(pod.config_hash, []).append(pod.name)
    desired = config_checksum(orchestrator.config_store[config_name])
    by_hash.setdefault(desired, [])
    return by_hash


class UpstreamPaymentsApi:
    """The third-party API: accepts only keys that have not been revoked."""

    def __init__(self) -> None:
        self.valid_keys: set[str] = set()

    def call(self, api_key: str) -> int:
        """Return an HTTP status: 200 for a valid key, 401 otherwise."""
        if api_key in self.valid_keys:
            return 200
        return 401


def failed_calls(pods: list[Pod], upstream: UpstreamPaymentsApi, key_name: str) -> int:
    """How many pods get a 401 when they call the upstream API with their key."""
    failures = 0
    for pod in pods:
        if upstream.call(pod.config_values[key_name]) != 200:
            failures += 1
    return failures

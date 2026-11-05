"""Pin container images to immutable digests so every node runs the release.

The original deploys referenced images by tag (``shop/api:v2``,
``shop/api:latest``). A tag is a movable pointer: when CI pushed a new build
under the same tag mid-rollout, nodes that pulled later ran different code
from nodes that pulled earlier, and "rolling back to the previous manifest"
re-pulled whatever the tag pointed to *now*, which was the bad build.

Fix: resolve every tag to its content digest (``sha256:...``) once, when the
release is created, and store the pinned manifest. Nodes pull by digest, so
the same release always means the same bytes, and ``find_drifted_nodes``
reports any node whose running digest differs from the release.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

DIGEST_PREFIX = "sha256:"
DEFAULT_TAG = "latest"   # what Docker assumes when a reference has no tag


class UnknownImage(Exception):
    pass


def split_reference(ref: str) -> tuple[str, str, str]:
    """Split an image reference into (repository, tag, digest); unused parts are "".

    ``registry:5000/shop/api:v2`` -> ("registry:5000/shop/api", "v2", "")
    ``shop/api@sha256:ab..``     -> ("shop/api", "", "sha256:ab..")
    ``shop/api``                 -> ("shop/api", "latest", "")
    ``shop/api:v2@sha256:ab..``  -> ("shop/api", "", "sha256:ab..")  (digest wins)
    """
    if "@" in ref:
        name, digest = ref.split("@", 1)
        repository, _tag, _ = split_reference(name)   # drop a tag written next to the digest
        return repository, "", digest
    # WHY: a ":" before the last "/" is a registry port, not a tag separator.
    last_slash = ref.rfind("/")
    last_colon = ref.rfind(":")
    if last_colon > last_slash:
        return ref[:last_colon], ref[last_colon + 1:], ""
    return ref, DEFAULT_TAG, ""


class FakeRegistry:
    """Container registry stand-in: content-addressed blobs plus movable tags."""

    def __init__(self) -> None:
        self.images: dict[str, set[str]] = {}       # repository -> digests stored
        self.tags: dict[tuple[str, str], str] = {}  # (repository, tag) -> digest

    def push(self, repository: str, tag: str, build_content: str) -> str:
        """Store a build and point ``tag`` at it (overwriting any old target)."""
        digest = DIGEST_PREFIX + hashlib.sha256(build_content.encode()).hexdigest()
        self.images.setdefault(repository, set()).add(digest)
        self.tags[(repository, tag)] = digest
        return digest

    def resolve(self, ref: str) -> str:
        """Return the digest ``ref`` points to right now."""
        repository, tag, digest = split_reference(ref)
        if digest:
            if digest not in self.images.get(repository, set()):
                raise UnknownImage(ref)
            return digest
        if (repository, tag) not in self.tags:
            raise UnknownImage(ref)
        return self.tags[(repository, tag)]


@dataclass
class FakeNode:
    """A host that pulls an image reference and runs whatever it got."""

    name: str
    running_digest: str = ""

    def deploy(self, ref: str, registry: FakeRegistry) -> None:
        self.running_digest = registry.resolve(ref)


@dataclass
class Release:
    version: str
    images: dict[str, str] = field(default_factory=dict)   # service -> image reference


def naive_release(version: str, images: dict[str, str]) -> Release:
    """Original behaviour: store the tags as written; resolve at pull time."""
    return Release(version, dict(images))


def pinned_reference(ref: str, registry: FakeRegistry) -> str:
    """Turn ``repo:tag`` into ``repo@sha256:...`` using the registry's current answer."""
    repository, _tag, _digest = split_reference(ref)
    return f"{repository}@{registry.resolve(ref)}"


def create_pinned_release(version: str, images: dict[str, str],
                          registry: FakeRegistry) -> Release:
    """Resolve every tag exactly once, now, and freeze the result in the release.

    An unknown tag raises here, at release time, instead of on the first
    node that tries to pull it.
    """
    pinned = {}
    for service, ref in images.items():
        pinned[service] = pinned_reference(ref, registry)
    return Release(version, pinned)


def rollout(release: Release, service: str, nodes: list[FakeNode],
            registry: FakeRegistry) -> None:
    """Deploy one service of ``release`` to every node, one node at a time."""
    for node in nodes:
        node.deploy(release.images[service], registry)


@dataclass
class Drift:
    node: str
    expected_digest: str
    running_digest: str


def find_drifted_nodes(release: Release, service: str, nodes: list[FakeNode],
                       registry: FakeRegistry) -> list[Drift]:
    """Nodes whose running digest differs from the one the release pins.

    For a pinned release ``resolve`` just checks the digest exists, so the
    expected value can never move under us.
    """
    expected = registry.resolve(release.images[service])
    drifted = []
    for node in nodes:
        if node.running_digest != expected:
            drifted.append(Drift(node.name, expected, node.running_digest))
    return drifted

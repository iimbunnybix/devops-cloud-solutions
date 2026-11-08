"""Size a worker's in-memory cache from the container's memory limit, not the host's RAM.

Image-processing workers sized their cache as half of "total memory", read
from /proc/meminfo. Inside a container that file shows the whole node
(64 GiB), not the container's 2 GiB cgroup limit, so each worker planned a
32 GiB cache and was OOMKilled by the kernel as soon as load filled it.

The fix reads the cgroup limit (v2 ``memory.max``, falling back to v1
``memory/memory.limit_in_bytes``; "max" or a huge v1 value means no limit),
takes the smaller of that and host memory, and reserves headroom for
everything that is not the cache. Paths are parameters, so tests use
temporary files instead of a real cgroup.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

MIB = 1024 * 1024
GIB = 1024 * MIB
KIB = 1024

# The original code gave half of "total memory" to the cache.
NAIVE_CACHE_FRACTION = 0.5
# Memory kept free for the runtime, buffers, request handling and spikes.
HEADROOM_FRACTION = 0.25
MIN_HEADROOM_BYTES = 512 * MIB
# cgroup v1 reports "no limit" as a page-aligned value near 2**63; anything
# this large cannot be a real limit.
CGROUP_V1_UNLIMITED_AT_LEAST = 2 ** 60
UNLIMITED_MARKER = "max"


def read_host_memory_bytes(meminfo_path: Path) -> int:
    """MemTotal from a /proc/meminfo-style file, in bytes (the whole node)."""
    for line in meminfo_path.read_text().splitlines():
        if line.startswith("MemTotal:"):
            kib = int(line.split()[1])
            return kib * KIB
    raise ValueError(f"no MemTotal line in {meminfo_path}")


def parse_cgroup_limit(text: str) -> int | None:
    """Turn the contents of a cgroup memory limit file into bytes or None (unlimited)."""
    value = text.strip()
    if value == UNLIMITED_MARKER:
        return None
    limit = int(value)          # malformed content raises: better than guessing
    if limit >= CGROUP_V1_UNLIMITED_AT_LEAST:
        return None
    return limit


def read_cgroup_limit_bytes(cgroup_root: Path) -> int | None:
    """Memory limit of the cgroup mounted at ``cgroup_root``, or None if there is none.

    cgroup v2 (unified) puts ``memory.max`` at the root; cgroup v1 has a
    separate ``memory`` controller directory with ``memory.limit_in_bytes``.
    """
    v2_file = cgroup_root / "memory.max"
    if v2_file.is_file():
        return parse_cgroup_limit(v2_file.read_text())
    v1_file = cgroup_root / "memory" / "memory.limit_in_bytes"
    if v1_file.is_file():
        return parse_cgroup_limit(v1_file.read_text())
    return None


def effective_memory_bytes(meminfo_path: Path, cgroup_root: Path) -> int:
    """Memory this process can really use: the smaller of host RAM and cgroup limit."""
    host = read_host_memory_bytes(meminfo_path)
    limit = read_cgroup_limit_bytes(cgroup_root)
    # A limit above host RAM is allowed by the kernel but cannot be reached.
    if limit is None or limit > host:
        return host
    return limit


def naive_cache_budget(meminfo_path: Path) -> int:
    """Original sizing: half of what /proc/meminfo says, i.e. half of the NODE."""
    return int(read_host_memory_bytes(meminfo_path) * NAIVE_CACHE_FRACTION)


def headroom_bytes(available: int) -> int:
    """Memory to leave for everything except the cache."""
    return max(MIN_HEADROOM_BYTES, int(available * HEADROOM_FRACTION))


def cache_budget(meminfo_path: Path, cgroup_root: Path) -> int:
    """Fixed sizing: effective memory minus headroom (never negative)."""
    available = effective_memory_bytes(meminfo_path, cgroup_root)
    return max(0, available - headroom_bytes(available))


class OOMKilled(Exception):
    """The kernel killed the container for exceeding its memory limit."""


@dataclass
class Container:
    """A container with a hard memory limit, like a cgroup with memory.max."""
    limit_bytes: int
    used_bytes: int = 0

    def allocate(self, size: int) -> None:
        """Charge memory to the container; over the limit the kernel kills it."""
        if self.used_bytes + size > self.limit_bytes:
            raise OOMKilled(f"used {self.used_bytes + size} > limit {self.limit_bytes}")
        self.used_bytes += size

    def free(self, size: int) -> None:
        """Return memory to the container."""
        self.used_bytes -= size


class Worker:
    """A worker with an LRU cache that evicts once it reaches ``cache_budget``."""

    def __init__(self, container: Container, cache_budget_bytes: int,
                 baseline_bytes: int) -> None:
        """Start the process (runtime, code, libraries use ``baseline_bytes``)."""
        self.container = container
        self.cache_budget_bytes = cache_budget_bytes
        self.cache: OrderedDict[str, int] = OrderedDict()
        self.cache_bytes = 0
        container.allocate(baseline_bytes)

    def handle(self, key: str, entry_bytes: int) -> None:
        """Serve one request, caching its result and evicting the oldest entries."""
        if key in self.cache:
            self.cache.move_to_end(key)
            return
        while self.cache and self.cache_bytes + entry_bytes > self.cache_budget_bytes:
            _, evicted_size = self.cache.popitem(last=False)
            self.cache_bytes -= evicted_size
            self.container.free(evicted_size)
        self.container.allocate(entry_bytes)
        self.cache[key] = entry_bytes
        self.cache_bytes += entry_bytes


def requests_until_oom(worker: Worker, requests: int, entry_bytes: int) -> int | None:
    """Send ``requests`` unique requests; return how many succeeded before OOM, or None."""
    for number in range(requests):
        try:
            worker.handle(f"image-{number}", entry_bytes)
        except OOMKilled:
            return number
    return None

# 040 · Image workers were OOMKilled over and over under load, although each one planned to use only "half the memory"

## Problem
A thumbnail service runs image-resizing workers on Kubernetes, packed onto
64 GiB nodes with a 2 GiB memory limit per pod. Each worker keeps decoded
source images in an in-memory LRU cache, because many thumbnails are made
from the same originals. At startup the worker sized that cache as "50% of
total memory", which it read from `/proc/meminfo`.

The service was fine in staging (small VMs, no limits). In production, as
soon as a traffic spike filled the caches, pods started dying with
`OOMKilled` (exit code 137), restarted with empty caches, filled up again and
died again. Thumbnails on product pages returned 502s or loaded seconds late,
and every restart threw away the cache and sent more load to the image
store.

## Root cause
**`/proc/meminfo` is not namespaced.** Inside a container it shows the
node's RAM, here **64 GiB**, not the container's **2 GiB** cgroup limit. The
worker therefore planned a **32 GiB** cache, 16 times its whole limit. The LRU
cache never evicted anything, because by its own budget it was nowhere near
full, and the kernel's cgroup OOM killer ended the process instead.

In `solution.py`'s model (300 MiB baseline per process, 1 MiB per cached
image, all requests for different images) the naive worker is **OOMKilled
after 1,748 requests**.

## Solution
`cache_budget` sizes the cache from the memory the process can really use:

1. `read_cgroup_limit_bytes` reads cgroup v2 `memory.max`; if that file is
   missing it falls back to cgroup v1 `memory/memory.limit_in_bytes`. The
   value `max` (v2), or v1's "no limit" value near 2^63, means unlimited.
2. `effective_memory_bytes` takes the smaller of that limit and `MemTotal`
   (a limit above the node's RAM can never be reached).
3. Headroom of 25%, and at least 512 MiB, is kept for the runtime, request
   buffers, decoding scratch space and spikes; the rest goes to the cache.

With a 2 GiB limit the budget is **1,536 MiB**. The same worker serves all
**20,000** requests, evicting old images, and its memory stays at **1,836
MiB**, under the limit. All paths are parameters, so the tests build fake
`meminfo` and cgroup files in a temporary directory.

## Trade-offs
- The paths are for a container that sees its own cgroup at
  `/sys/fs/cgroup` (the default with cgroup namespaces). On a host, or with
  an unusual mount, find the process's cgroup in `/proc/self/cgroup` first.
- The limit is read once at startup. If the limit is changed in place (a
  resize without a restart), the budget is stale; re-read it periodically if
  your platform does that.
- Headroom is a guess; measure peak non-cache memory (RSS minus cache size)
  under load and set the fraction from that. Memory used by the kernel for
  this cgroup (page cache, socket buffers) also counts towards the limit.
- Language runtimes have their own versions of this bug: modern JVMs
  (`-XX:MaxRAMPercentage`) and Go (`GOMEMLIMIT`) can respect cgroup limits,
  but only if configured or new enough. Check what yours does.
- A per-entry byte count is an estimate; Python objects carry overhead, so
  cap the cache by measured bytes, not by entry count.

## Run
```bash
python -m unittest -v test_solution.py
```

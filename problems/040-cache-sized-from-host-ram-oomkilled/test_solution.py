import tempfile
import unittest
from pathlib import Path

from solution import (GIB, KIB, MIB, MIN_HEADROOM_BYTES, Container, Worker, cache_budget,
                      effective_memory_bytes, naive_cache_budget, parse_cgroup_limit,
                      read_cgroup_limit_bytes, read_host_memory_bytes, requests_until_oom)

NODE_RAM = 64 * GIB
CONTAINER_LIMIT = 2 * GIB
BASELINE = 300 * MIB          # runtime, libraries and request buffers
ENTRY = 1 * MIB               # one cached decoded image
REQUESTS = 20_000
V1_UNLIMITED = "9223372036854771712"


class FakeNode:
    """Temporary /proc/meminfo and cgroup files for one simulated container."""

    def __init__(self, host_bytes: int = NODE_RAM) -> None:
        """Create the directory tree with a meminfo file for ``host_bytes``."""
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.meminfo = root / "meminfo"
        self.meminfo.write_text(f"MemTotal:       {host_bytes // KIB} kB\n"
                                f"MemFree:        {host_bytes // KIB // 2} kB\n")
        self.cgroup = root / "cgroup"
        self.cgroup.mkdir()

    def set_v2_limit(self, text: str) -> None:
        """Write cgroup v2 memory.max."""
        (self.cgroup / "memory.max").write_text(text + "\n")

    def set_v1_limit(self, text: str) -> None:
        """Write cgroup v1 memory/memory.limit_in_bytes."""
        (self.cgroup / "memory").mkdir(exist_ok=True)
        (self.cgroup / "memory" / "memory.limit_in_bytes").write_text(text + "\n")

    def cleanup(self) -> None:
        """Delete the temporary files."""
        self._tmp.cleanup()


def run_worker(budget: int) -> tuple[int | None, Container]:
    """Run the load test in a 2 GiB container with the given cache budget."""
    container = Container(CONTAINER_LIMIT)
    worker = Worker(container, budget, BASELINE)
    return requests_until_oom(worker, REQUESTS, ENTRY), container


class NodeTestCase(unittest.TestCase):
    def setUp(self):
        self.node = FakeNode()
        self.addCleanup(self.node.cleanup)


class ReproduceProblem(NodeTestCase):
    def test_meminfo_reports_the_whole_node_inside_a_container(self):
        self.node.set_v2_limit(str(CONTAINER_LIMIT))
        self.assertEqual(read_host_memory_bytes(self.node.meminfo), NODE_RAM)

    def test_naive_cache_budget_is_sixteen_times_the_container_limit(self):
        self.assertEqual(naive_cache_budget(self.node.meminfo), 32 * GIB)

    def test_naive_worker_is_oomkilled_after_1748_requests(self):
        served, _ = run_worker(naive_cache_budget(self.node.meminfo))
        self.assertEqual(served, 1_748)


class FixCgroupAwareSizing(NodeTestCase):
    def test_cgroup_v2_limit_is_used(self):
        self.node.set_v2_limit(str(CONTAINER_LIMIT))
        self.assertEqual(effective_memory_bytes(self.node.meminfo, self.node.cgroup),
                         CONTAINER_LIMIT)

    def test_budget_reserves_headroom_below_the_limit(self):
        self.node.set_v2_limit(str(CONTAINER_LIMIT))
        self.assertEqual(cache_budget(self.node.meminfo, self.node.cgroup), 1_536 * MIB)

    def test_fixed_worker_serves_all_requests_without_oom(self):
        self.node.set_v2_limit(str(CONTAINER_LIMIT))
        served, container = run_worker(cache_budget(self.node.meminfo, self.node.cgroup))
        self.assertIsNone(served)
        self.assertEqual(container.used_bytes, 1_836 * MIB)

    def test_v2_max_means_unlimited_and_falls_back_to_host(self):
        self.node.set_v2_limit("max")
        self.assertEqual(effective_memory_bytes(self.node.meminfo, self.node.cgroup),
                         NODE_RAM)

    def test_v1_limit_is_used_when_there_is_no_v2_file(self):
        self.node.set_v1_limit(str(CONTAINER_LIMIT))
        self.assertEqual(read_cgroup_limit_bytes(self.node.cgroup), CONTAINER_LIMIT)

    def test_v1_huge_value_means_unlimited(self):
        self.node.set_v1_limit(V1_UNLIMITED)
        self.assertIsNone(read_cgroup_limit_bytes(self.node.cgroup))

    def test_v2_wins_when_both_files_exist(self):
        self.node.set_v2_limit(str(CONTAINER_LIMIT))
        self.node.set_v1_limit(str(8 * GIB))
        self.assertEqual(read_cgroup_limit_bytes(self.node.cgroup), CONTAINER_LIMIT)

    def test_no_cgroup_files_means_host_memory(self):
        self.assertEqual(effective_memory_bytes(self.node.meminfo, self.node.cgroup),
                         NODE_RAM)

    def test_limit_above_host_ram_is_capped_at_host_ram(self):
        self.node.set_v2_limit(str(128 * GIB))
        self.assertEqual(effective_memory_bytes(self.node.meminfo, self.node.cgroup),
                         NODE_RAM)

    def test_large_limit_reserves_a_quarter_as_headroom(self):
        self.node.set_v2_limit(str(8 * GIB))
        self.assertEqual(cache_budget(self.node.meminfo, self.node.cgroup), 6 * GIB)

    def test_tiny_limit_gives_zero_cache_not_negative(self):
        self.node.set_v2_limit(str(MIN_HEADROOM_BYTES // 2))
        self.assertEqual(cache_budget(self.node.meminfo, self.node.cgroup), 0)

    def test_malformed_limit_is_an_error(self):
        self.assertRaises(ValueError, parse_cgroup_limit, "two gigabytes")

    def test_missing_memtotal_is_an_error(self):
        self.node.meminfo.write_text("MemFree: 100 kB\n")
        self.assertRaises(ValueError, read_host_memory_bytes, self.node.meminfo)


class WorkerCache(unittest.TestCase):
    def test_cache_hit_does_not_allocate(self):
        container = Container(GIB)
        worker = Worker(container, 10 * MIB, BASELINE)
        worker.handle("a", ENTRY)
        worker.handle("a", ENTRY)
        self.assertEqual(container.used_bytes, BASELINE + ENTRY)

    def test_oldest_entry_is_evicted_at_the_budget(self):
        worker = Worker(Container(GIB), 2 * MIB, BASELINE)
        for key in ("a", "b", "c"):
            worker.handle(key, ENTRY)
        self.assertEqual(list(worker.cache), ["b", "c"])


if __name__ == "__main__":
    unittest.main()

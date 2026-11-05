import unittest
from collections import Counter

from solution import (Node, UnschedulableError, naive_bin_pack, replicas_left_after_zone_loss,
                      spread_place, survives_any_zone_loss)

REPLICA_CPU_MILLIS = 500
REPLICAS = 6
MIN_AVAILABLE = 4     # login needs 4 replicas to carry peak traffic


def cluster() -> list[Node]:
    """Three zones; zone-a's node is already busier, so bin-packing prefers it."""
    return [
        Node("a-1", "zone-a", 3_000),
        Node("b-1", "zone-b", 4_000),
        Node("c-1", "zone-c", 4_000),
    ]


def replicas_per_zone(placement: dict[str, str], nodes: list[Node]) -> Counter:
    zone_of_node = {node.name: node.zone for node in nodes}
    return Counter(zone_of_node[name] for name in placement.values())


class ReproduceProblem(unittest.TestCase):
    def test_bin_packing_puts_every_replica_in_one_zone(self):
        nodes = cluster()
        placement = naive_bin_pack(nodes, REPLICAS, REPLICA_CPU_MILLIS)
        self.assertEqual(replicas_per_zone(placement, nodes), Counter({"zone-a": 6}))

    def test_losing_that_zone_leaves_no_replicas(self):
        nodes = cluster()
        placement = naive_bin_pack(nodes, REPLICAS, REPLICA_CPU_MILLIS)
        self.assertEqual(replicas_left_after_zone_loss(placement, nodes)["zone-a"], 0)
        self.assertFalse(survives_any_zone_loss(placement, nodes, MIN_AVAILABLE))


class FixZoneSpread(unittest.TestCase):
    def test_replicas_are_spread_evenly_across_zones(self):
        nodes = cluster()
        placement = spread_place(nodes, REPLICAS, REPLICA_CPU_MILLIS)
        self.assertEqual(replicas_per_zone(placement, nodes),
                         Counter({"zone-a": 2, "zone-b": 2, "zone-c": 2}))

    def test_spread_placement_survives_any_single_zone_loss(self):
        nodes = cluster()
        placement = spread_place(nodes, REPLICAS, REPLICA_CPU_MILLIS)
        self.assertTrue(survives_any_zone_loss(placement, nodes, MIN_AVAILABLE))

    def test_uneven_count_stays_within_max_skew(self):
        nodes = cluster()
        placement = spread_place(nodes, 7, REPLICA_CPU_MILLIS)
        counts = replicas_per_zone(placement, nodes)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_bin_packing_is_kept_inside_a_zone(self):
        nodes = [Node("a-big", "zone-a", 8_000), Node("a-small", "zone-a", 1_000),
                 Node("b-1", "zone-b", 4_000)]
        placement = spread_place(nodes, 2, REPLICA_CPU_MILLIS)
        self.assertEqual(placement["replica-0"], "a-small")

    def test_full_zone_blocks_placement_instead_of_packing_the_others(self):
        nodes = [Node("a-1", "zone-a", 4_000), Node("b-1", "zone-b", 4_000),
                 Node("c-1", "zone-c", 0)]
        with self.assertRaises(UnschedulableError):
            spread_place(nodes, REPLICAS, REPLICA_CPU_MILLIS)

    def test_larger_max_skew_allows_more_imbalance(self):
        nodes = [Node("a-1", "zone-a", 4_000), Node("b-1", "zone-b", 500)]
        with self.assertRaises(UnschedulableError):
            spread_place(nodes, 4, REPLICA_CPU_MILLIS, max_skew=1)
        nodes = [Node("a-1", "zone-a", 4_000), Node("b-1", "zone-b", 500)]
        placement = spread_place(nodes, 4, REPLICA_CPU_MILLIS, max_skew=2)
        self.assertEqual(replicas_per_zone(placement, nodes),
                         Counter({"zone-a": 3, "zone-b": 1}))

    def test_max_skew_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            spread_place(cluster(), REPLICAS, REPLICA_CPU_MILLIS, max_skew=0)

    def test_single_zone_cluster_cannot_survive_zone_loss(self):
        nodes = [Node("a-1", "zone-a", 8_000)]
        placement = spread_place(nodes, REPLICAS, REPLICA_CPU_MILLIS)
        self.assertFalse(survives_any_zone_loss(placement, nodes, MIN_AVAILABLE))


if __name__ == "__main__":
    unittest.main()

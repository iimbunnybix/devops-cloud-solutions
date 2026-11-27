import unittest

from solution import (CATCH_UP_SECONDS, PROCESS_START_SECONDS, FakeCluster, health_gated_rolling_restart,
                      make_cluster, naive_rolling_restart)


def cluster_with_already_crashed_nodes(size: int, crashed: int) -> FakeCluster:
    cluster = make_cluster(size)
    for node in cluster.nodes[-crashed:]:
        node.down = True
    return cluster


def run_and_finish(cluster: FakeCluster, rollout) -> None:
    """Run a rollout, then let the cluster settle so all outage time is counted."""
    rollout(cluster)
    for _ in range(PROCESS_START_SECONDS + CATCH_UP_SECONDS):
        cluster.tick()


class ReproduceProblem(unittest.TestCase):
    def test_naive_restart_of_three_nodes_loses_quorum_for_35_seconds(self):
        cluster = make_cluster(3)
        run_and_finish(cluster, naive_rolling_restart)
        self.assertEqual(cluster.seconds_without_quorum, 35)

    def test_naive_restart_finishes_all_three_nodes_in_15_seconds(self):
        cluster = make_cluster(3)
        naive_rolling_restart(cluster)
        self.assertEqual(cluster.clock.now, 3 * PROCESS_START_SECONDS)

    def test_open_port_does_not_mean_the_node_is_back_in_sync(self):
        cluster = make_cluster(3)
        first = cluster.nodes[0]
        first.restart(0)
        cluster.clock.advance(PROCESS_START_SECONDS)
        self.assertTrue(first.port_open(cluster.clock.now))
        self.assertFalse(first.in_sync(cluster.clock.now))

    def test_naive_restart_of_five_nodes_also_loses_quorum(self):
        cluster = make_cluster(5)
        run_and_finish(cluster, naive_rolling_restart)
        self.assertEqual(cluster.seconds_without_quorum, 35)


class FixHealthGatedRestart(unittest.TestCase):
    def test_three_node_cluster_keeps_quorum_throughout(self):
        cluster = make_cluster(3)
        run_and_finish(cluster, health_gated_rolling_restart)
        self.assertEqual(cluster.seconds_without_quorum, 0)

    def test_every_node_is_restarted(self):
        result = health_gated_rolling_restart(make_cluster(3))
        self.assertEqual((result.restarted, result.aborted_reason),
                         (["node-1", "node-2", "node-3"], None))

    def test_rollout_takes_35_seconds_per_node(self):
        cluster = make_cluster(3)
        health_gated_rolling_restart(cluster)
        self.assertEqual(cluster.clock.now, 3 * (PROCESS_START_SECONDS + CATCH_UP_SECONDS))

    def test_five_node_cluster_keeps_quorum_throughout(self):
        cluster = make_cluster(5)
        run_and_finish(cluster, health_gated_rolling_restart)
        self.assertEqual(cluster.seconds_without_quorum, 0)

    def test_node_that_never_catches_up_aborts_the_rollout(self):
        cluster = make_cluster(3)
        cluster.nodes[0].catch_up_seconds = None
        result = health_gated_rolling_restart(cluster)
        self.assertEqual((result.restarted, result.aborted_reason),
                         (["node-1"], "node-1 not in sync after 120s"))

    def test_abort_leaves_the_other_nodes_serving_with_quorum(self):
        cluster = make_cluster(3)
        cluster.nodes[0].catch_up_seconds = None
        run_and_finish(cluster, health_gated_rolling_restart)
        self.assertEqual(cluster.seconds_without_quorum, 0)

    def test_does_not_start_when_a_node_is_already_down_in_a_three_node_cluster(self):
        cluster = cluster_with_already_crashed_nodes(size=3, crashed=1)
        result = health_gated_rolling_restart(cluster)
        self.assertEqual((result.restarted, result.aborted_reason),
                         ([], "not restarting node-1: only 1 other in-sync voters, need 2"))

    def test_five_node_cluster_with_one_node_down_can_still_roll(self):
        cluster = cluster_with_already_crashed_nodes(size=5, crashed=1)
        result = health_gated_rolling_restart(cluster)
        self.assertEqual(result.restarted, ["node-1", "node-2", "node-3", "node-4", "node-5"])

    def test_five_node_cluster_with_one_node_down_never_loses_quorum(self):
        cluster = cluster_with_already_crashed_nodes(size=5, crashed=1)
        run_and_finish(cluster, health_gated_rolling_restart)
        self.assertEqual(cluster.seconds_without_quorum, 0)

    def test_five_node_cluster_with_two_nodes_down_does_not_start(self):
        cluster = cluster_with_already_crashed_nodes(size=5, crashed=2)
        result = health_gated_rolling_restart(cluster)
        self.assertEqual(result.restarted, [])

    def test_waits_for_a_recovering_node_before_restarting_the_next(self):
        cluster = make_cluster(3)
        cluster.nodes[2].restart(0)  # node-3 is still catching up from an earlier restart
        health_gated_rolling_restart(cluster)
        # node-1 cannot go until node-3 is in sync at 35 s, then 3 x 35 s more.
        self.assertEqual(cluster.clock.now, 35 + 3 * 35)


if __name__ == "__main__":
    unittest.main()

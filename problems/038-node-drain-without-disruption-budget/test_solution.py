import unittest

from solution import (BUDGETS, DRAIN_TIMEOUT_SECONDS, UPGRADE_BATCHES, Cluster, Pod,
                      budget_eviction_allowed, check_budgets, drain_cluster,
                      example_cluster, naive_eviction_allowed)


def drain(policy, budgets=None):
    """Upgrade the example cluster in two batches of two nodes."""
    if budgets is None:
        budgets = BUDGETS
    return drain_cluster(example_cluster(), UPGRADE_BATCHES, policy, budgets)


class ReproduceProblem(unittest.TestCase):
    def test_naive_drain_leaves_login_with_zero_ready_pods(self):
        self.assertEqual(drain(naive_eviction_allowed).min_ready["login"], 0)

    def test_login_is_down_in_both_batches_for_110_seconds(self):
        self.assertEqual(drain(naive_eviction_allowed).zero_ready_seconds["login"], 110)

    def test_every_service_has_an_outage(self):
        result = drain(naive_eviction_allowed)
        for service in ("login", "checkout", "search"):
            self.assertEqual(result.min_ready[service], 0, service)


class FixDisruptionBudget(unittest.TestCase):
    def test_budgeted_drain_completes(self):
        self.assertTrue(drain(budget_eviction_allowed).completed)

    def test_login_never_drops_below_its_budget(self):
        self.assertEqual(drain(budget_eviction_allowed).min_ready["login"], 2)

    def test_no_service_ever_reaches_zero_ready_pods(self):
        result = drain(budget_eviction_allowed)
        self.assertEqual(set(result.zero_ready_seconds.values()), {0})

    def test_budgeted_drain_is_slower_because_evictions_wait_for_replacements(self):
        self.assertEqual(drain(naive_eviction_allowed).finished_at, 250)
        self.assertEqual(drain(budget_eviction_allowed).finished_at, 490)

    def test_upgraded_nodes_hold_no_pods_from_before_the_drain(self):
        cluster = example_cluster()
        drain_cluster(cluster, UPGRADE_BATCHES, budget_eviction_allowed, BUDGETS)
        self.assertEqual(len(cluster.pods), 7)
        self.assertTrue(all(pod.ready_at > 0 for pod in cluster.pods))


class EvictionDecision(unittest.TestCase):
    def test_eviction_refused_when_it_would_break_the_budget(self):
        cluster = Cluster(["n1", "n2"], [Pod("login", "n1"), Pod("login", "n2")])
        allowed = budget_eviction_allowed(cluster, cluster.pods[0], 0, {"login": 2})
        self.assertFalse(allowed)

    def test_pod_that_is_not_ready_yet_can_always_be_evicted(self):
        starting = Pod("login", "n1", ready_at=100)
        cluster = Cluster(["n1", "n2"], [starting, Pod("login", "n2")])
        self.assertTrue(budget_eviction_allowed(cluster, starting, 0, {"login": 1}))


class BlockedDrain(unittest.TestCase):
    def test_budget_equal_to_replicas_blocks_and_reports_stuck_pods(self):
        result = drain(budget_eviction_allowed, {"login": 3})
        self.assertFalse(result.completed)
        self.assertEqual(result.finished_at, DRAIN_TIMEOUT_SECONDS)
        self.assertEqual(sorted(result.blocked_pods), ["login@n1", "login@n1", "login@n2"])

    def test_lint_flags_missing_and_impossible_budgets(self):
        problems = check_budgets({"login": 3, "search": 2, "checkout": 2},
                                 {"login": 3, "checkout": 1})
        self.assertEqual(len(problems), 2)
        self.assertIn("login: min_available=3", problems[0])
        self.assertIn("search: no disruption budget", problems[1])

    def test_lint_accepts_the_example_budgets(self):
        self.assertEqual(check_budgets({"login": 3, "checkout": 2, "search": 2}, BUDGETS), [])


if __name__ == "__main__":
    unittest.main()

import unittest

from solution import (MAX_RESTARTS_PER_WINDOW, FakeService, RemediationPolicy, run_budgeted_bot,
                      run_naive_bot)


def disk_full_service() -> FakeService:
    return FakeService(persistent_fault=True)


def stuck_process_service() -> FakeService:
    return FakeService(persistent_fault=False)


class ReproduceProblem(unittest.TestCase):
    def test_persistent_fault_is_restarted_every_minute(self):
        self.assertEqual(run_naive_bot(disk_full_service()).restarts, 110)

    def test_persistent_fault_never_pages_anyone(self):
        self.assertEqual(run_naive_bot(disk_full_service()).pages, [])

    def test_naive_bot_does_cure_a_stuck_process(self):
        self.assertEqual(run_naive_bot(stuck_process_service()).unhealthy_minutes, 1)


class FixRestartBudget(unittest.TestCase):
    def test_stuck_process_is_still_cured_by_one_restart_without_a_page(self):
        result = run_budgeted_bot(stuck_process_service(), RemediationPolicy())
        self.assertEqual((result.restarts, result.pages), (1, []))

    def test_persistent_fault_gets_only_the_restart_budget(self):
        result = run_budgeted_bot(disk_full_service(), RemediationPolicy())
        self.assertEqual(result.restarts, MAX_RESTARTS_PER_WINDOW)

    def test_persistent_fault_pages_exactly_once_with_context(self):
        result = run_budgeted_bot(disk_full_service(), RemediationPolicy())
        self.assertEqual(result.pages,
                         ["minute 13: still unhealthy after 3 restarts in 30 min; auto-restart paused"])

    def test_bot_waits_after_paging(self):
        policy = RemediationPolicy(max_restarts=1)
        self.assertEqual(policy.decide(0, healthy=False), "restart")
        self.assertEqual(policy.decide(1, healthy=False), "page")
        self.assertEqual(policy.decide(2, healthy=False), "wait")

    def test_restarts_spread_beyond_the_window_do_not_exhaust_the_budget(self):
        policy = RemediationPolicy(max_restarts=1, window_minutes=30)
        self.assertEqual(policy.decide(0, healthy=False), "restart")
        self.assertEqual(policy.decide(30, healthy=False), "restart")

    def test_restart_exactly_at_window_edge_has_expired(self):
        policy = RemediationPolicy(max_restarts=1, window_minutes=30)
        policy.decide(0, healthy=False)
        self.assertEqual(policy.decide(29, healthy=False), "page")

    def test_acknowledge_restores_the_budget(self):
        policy = RemediationPolicy(max_restarts=1)
        policy.decide(0, healthy=False)
        policy.decide(1, healthy=False)
        policy.acknowledge()
        self.assertEqual(policy.decide(2, healthy=False), "restart")

    def test_healthy_check_needs_no_action(self):
        self.assertEqual(RemediationPolicy().decide(0, healthy=True), "ok")


if __name__ == "__main__":
    unittest.main()

import unittest

from solution import (CREATE, DESTROY, PlanBlocked, check_plan, count_actions, destroyed_names,
                      key_addresses, keyed_plan, naive_plan)

QUEUES = ["orders", "payments", "billing", "emails", "refunds", "exports"]
WITHOUT_PAYMENTS = ["orders", "billing", "emails", "refunds", "exports"]


class ReproduceProblem(unittest.TestCase):
    def test_removing_one_queue_recreates_every_later_queue(self):
        plan = naive_plan(QUEUES, WITHOUT_PAYMENTS)
        self.assertEqual(destroyed_names(plan),
                         ["payments", "billing", "emails", "refunds", "exports"])

    def test_plan_summary_shows_four_creates_and_five_destroys(self):
        self.assertEqual(count_actions(naive_plan(QUEUES, WITHOUT_PAYMENTS)),
                         {CREATE: 4, DESTROY: 5})

    def test_removing_the_last_queue_is_harmless_even_with_indexes(self):
        plan = naive_plan(QUEUES, QUEUES[:-1])
        self.assertEqual(destroyed_names(plan), ["exports"])


class FixKeyedAddresses(unittest.TestCase):
    def test_removing_one_queue_destroys_only_that_queue(self):
        plan = keyed_plan(QUEUES, WITHOUT_PAYMENTS)
        self.assertEqual(destroyed_names(plan), ["payments"])
        self.assertEqual(count_actions(plan), {CREATE: 0, DESTROY: 1})

    def test_inserting_a_queue_in_the_middle_only_creates_it(self):
        desired = QUEUES[:2] + ["audit"] + QUEUES[2:]
        plan = keyed_plan(QUEUES, desired)
        self.assertEqual(len(plan), 1)
        self.assertEqual((plan[0].action, plan[0].name), (CREATE, "audit"))

    def test_reordering_the_list_changes_nothing(self):
        self.assertEqual(keyed_plan(QUEUES, list(reversed(QUEUES))), [])

    def test_duplicate_names_cannot_be_keys(self):
        with self.assertRaises(ValueError):
            key_addresses(["orders", "orders"])

    def test_empty_to_full_creates_everything(self):
        self.assertEqual(count_actions(keyed_plan([], QUEUES)), {CREATE: 6, DESTROY: 0})


class FixProtectedResourceGuard(unittest.TestCase):
    def test_index_plan_is_blocked_because_it_destroys_billing(self):
        with self.assertRaisesRegex(PlanBlocked, "billing"):
            check_plan(naive_plan(QUEUES, WITHOUT_PAYMENTS), protected={"billing"}, approved=set())

    def test_keyed_plan_passes_when_only_unprotected_queue_goes(self):
        check_plan(keyed_plan(QUEUES, WITHOUT_PAYMENTS), protected={"billing"}, approved=set())

    def test_destroying_a_protected_queue_needs_explicit_approval(self):
        plan = keyed_plan(QUEUES, WITHOUT_PAYMENTS)
        with self.assertRaises(PlanBlocked):
            check_plan(plan, protected={"payments"}, approved=set())
        check_plan(plan, protected={"payments"}, approved={"payments"})

    def test_blocked_message_lists_every_protected_name(self):
        with self.assertRaisesRegex(PlanBlocked, "billing, emails"):
            check_plan(naive_plan(QUEUES, WITHOUT_PAYMENTS),
                       protected={"emails", "billing"}, approved=set())


if __name__ == "__main__":
    unittest.main()

import unittest

from solution import (ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE, State,
                      StalePlanError, make_plan, naive_apply, safe_apply)

LINEAGE = "lineage-prod-web"


def production_state() -> State:
    """State at 10:00: web runs on m5.large and only allows port 80."""
    return State(LINEAGE, serial=41, resources={
        "web": {"instance_type": "m5.large", "ingress_ports": "80"},
        "db": {"instance_class": "db.r5.xlarge"},
    })


def alice_desired_config() -> dict[str, dict[str, str]]:
    """Alice's branch opens port 443; it still has the old instance type."""
    return {
        "web": {"instance_type": "m5.large", "ingress_ports": "80,443"},
        "db": {"instance_class": "db.r5.xlarge"},
    }


def bob_emergency_scale_up(state: State) -> None:
    """At 10:30 Bob plans and applies a bigger instance type for web."""
    bob_config = {
        "web": {"instance_type": "m5.2xlarge", "ingress_ports": "80"},
        "db": {"instance_class": "db.r5.xlarge"},
    }
    safe_apply(state, make_plan(state, bob_config))


class ReproduceProblem(unittest.TestCase):
    def test_stale_plan_silently_reverts_teammates_scale_up(self):
        state = production_state()
        alice_plan = make_plan(state, alice_desired_config())
        bob_emergency_scale_up(state)
        naive_apply(state, alice_plan)
        self.assertEqual(state.resources["web"]["instance_type"], "m5.large")
        self.assertEqual(state.resources["web"]["ingress_ports"], "80,443")


class FixRefuseStalePlans(unittest.TestCase):
    def test_plan_made_before_teammates_apply_is_rejected(self):
        state = production_state()
        alice_plan = make_plan(state, alice_desired_config())
        bob_emergency_scale_up(state)
        with self.assertRaises(StalePlanError):
            safe_apply(state, alice_plan)
        self.assertEqual(state.resources["web"]["instance_type"], "m5.2xlarge")

    def test_rejected_apply_leaves_state_and_serial_untouched(self):
        state = production_state()
        alice_plan = make_plan(state, alice_desired_config())
        bob_emergency_scale_up(state)
        serial_after_bob = state.serial
        with self.assertRaises(StalePlanError):
            safe_apply(state, alice_plan)
        self.assertEqual(state.serial, serial_after_bob)

    def test_replanning_shows_the_conflict_in_the_diff(self):
        state = production_state()
        bob_emergency_scale_up(state)
        fresh_plan = make_plan(state, alice_desired_config())
        web_change = fresh_plan.changes[0]
        self.assertEqual(web_change.before["instance_type"], "m5.2xlarge")
        self.assertEqual(web_change.after["instance_type"], "m5.large")

    def test_current_plan_applies_and_bumps_serial(self):
        state = production_state()
        safe_apply(state, make_plan(state, alice_desired_config()))
        self.assertEqual(state.resources["web"]["ingress_ports"], "80,443")
        self.assertEqual(state.serial, 42)

    def test_same_plan_cannot_be_applied_twice(self):
        state = production_state()
        plan = make_plan(state, alice_desired_config())
        safe_apply(state, plan)
        with self.assertRaises(StalePlanError):
            safe_apply(state, plan)

    def test_plan_for_another_state_lineage_is_rejected(self):
        staging = State("lineage-staging-web", serial=41,
                        resources=production_state().resources)
        plan = make_plan(staging, alice_desired_config())
        with self.assertRaises(StalePlanError):
            safe_apply(production_state(), plan)

    def test_hand_edited_state_with_same_serial_is_rejected(self):
        state = production_state()
        plan = make_plan(state, alice_desired_config())
        state.resources["db"]["instance_class"] = "db.r5.4xlarge"
        with self.assertRaises(StalePlanError):
            safe_apply(state, plan)


class FixPlanContents(unittest.TestCase):
    def test_plan_lists_create_update_and_delete(self):
        state = production_state()
        desired = {"web": {"instance_type": "m5.large", "ingress_ports": "80,443"},
                   "cache": {"node_type": "cache.t3.small"}}
        actions = {change.resource_name: change.action
                   for change in make_plan(state, desired).changes}
        self.assertEqual(actions, {"cache": ACTION_CREATE, "db": ACTION_DELETE,
                                   "web": ACTION_UPDATE})

    def test_no_op_plan_has_no_changes(self):
        state = production_state()
        self.assertEqual(make_plan(state, dict(state.resources)).changes, ())


if __name__ == "__main__":
    unittest.main()

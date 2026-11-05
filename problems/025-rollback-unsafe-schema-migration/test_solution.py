import unittest

from solution import (
    AppVersion,
    Column,
    Operation,
    apply_migration,
    naive_deploy_gate,
    rollback_safe_deploy_gate,
)


def current_schema():
    return {
        "orders": {
            "id": Column("id", nullable=False, has_default=True),
            "coupon_code": Column("coupon_code"),
            "total_cents": Column("total_cents", nullable=False),
        }
    }


RELEASE_41 = AppVersion(
    "release-41",
    reads={"orders": {"id", "coupon_code", "total_cents"}},
    inserts={"orders": {"coupon_code", "total_cents"}},
)

RELEASE_42 = AppVersion(
    "release-42",
    reads={"orders": {"id", "promo_code", "total_cents"}},
    inserts={"orders": {"promo_code", "total_cents"}},
)

RENAME_COUPON = [Operation("rename_column", "orders", Column("coupon_code"), new_name="promo_code")]


class ReproduceProblem(unittest.TestCase):
    def test_naive_gate_approves_a_rename_that_breaks_rollback(self):
        self.assertEqual(naive_deploy_gate(RELEASE_42, current_schema(), RENAME_COUPON), [])

    def test_old_release_cannot_read_the_renamed_column(self):
        migrated = apply_migration(current_schema(), RENAME_COUPON)
        self.assertNotIn("coupon_code", migrated["orders"])


class FixBlocksRollbackBreakingMigrations(unittest.TestCase):
    def test_rename_of_a_column_the_old_release_uses_is_blocked(self):
        problems = rollback_safe_deploy_gate(RELEASE_41, RELEASE_42, current_schema(), RENAME_COUPON)
        self.assertEqual(problems,
                         ["rollback blocked: release-41 reads orders.coupon_code, which does not exist"])

    def test_drop_of_a_column_the_old_release_uses_is_blocked(self):
        drop = [Operation("drop_column", "orders", Column("coupon_code"))]
        new_app = AppVersion("release-42", reads={"orders": {"id", "total_cents"}})
        problems = rollback_safe_deploy_gate(RELEASE_41, new_app, current_schema(), drop)
        self.assertEqual(len(problems), 1)
        self.assertIn("coupon_code", problems[0])

    def test_required_column_the_old_release_does_not_insert_is_blocked(self):
        add = [Operation("add_column", "orders", Column("currency", nullable=False))]
        new_app = AppVersion("release-42", inserts={"orders": {"coupon_code", "total_cents", "currency"}})
        problems = rollback_safe_deploy_gate(RELEASE_41, new_app, current_schema(), add)
        self.assertEqual(problems, ["rollback blocked: release-41 inserts into orders without "
                                    "currency, which is NOT NULL with no default"])


class FixAllowsExpandContractSteps(unittest.TestCase):
    def test_expand_step_adding_a_nullable_column_is_allowed(self):
        expand = [Operation("add_column", "orders", Column("promo_code"))]
        problems = rollback_safe_deploy_gate(RELEASE_41, RELEASE_41, current_schema(), expand)
        self.assertEqual(problems, [])

    def test_required_column_with_a_default_is_allowed(self):
        add = [Operation("add_column", "orders", Column("currency", nullable=False, has_default=True))]
        self.assertEqual(rollback_safe_deploy_gate(RELEASE_41, RELEASE_41, current_schema(), add), [])

    def test_contract_step_after_old_release_is_gone_is_allowed(self):
        # Later deploy: both the running and the rollback release use promo_code.
        schema = apply_migration(current_schema(),
                                 [Operation("add_column", "orders", Column("promo_code"))])
        drop_old = [Operation("drop_column", "orders", Column("coupon_code"))]
        self.assertEqual(rollback_safe_deploy_gate(RELEASE_42, RELEASE_42, schema, drop_old), [])

    def test_new_release_problems_are_still_reported(self):
        problems = rollback_safe_deploy_gate(RELEASE_41, RELEASE_42, current_schema(), [])
        self.assertEqual(problems, ["release-42 reads orders.promo_code, which does not exist"])

    def test_apply_migration_does_not_modify_the_input_schema(self):
        schema = current_schema()
        apply_migration(schema, RENAME_COUPON)
        self.assertIn("coupon_code", schema["orders"])

    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_migration(current_schema(), [Operation("truncate", "orders", Column("id"))])


if __name__ == "__main__":
    unittest.main()

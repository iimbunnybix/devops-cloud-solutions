import unittest

from solution import (CREATE, DELETE, ResourceChange, find_renames, naive_can_auto_apply,
                      review_plan)

DB_CONFIG = {"engine": "postgres", "instance_class": "db.r6g.large", "allocated_storage": 500}


def db_attributes(**overrides) -> dict:
    """Attributes of the orders database, with computed fields filled in."""
    attributes = {**DB_CONFIG, "id": "orders-db-1", "endpoint": "orders-db-1.internal:5432"}
    attributes.update(overrides)
    return attributes


def rename_plan() -> list[ResourceChange]:
    """Plan produced after renaming aws_db_instance.main to aws_db_instance.primary."""
    return [
        ResourceChange("aws_db_instance.main", "aws_db_instance", [DELETE],
                       before=db_attributes()),
        ResourceChange("aws_db_instance.primary", "aws_db_instance", [CREATE],
                       after=dict(DB_CONFIG)),
    ]


class ReproduceProblem(unittest.TestCase):
    def test_naive_gate_auto_applies_a_plan_that_deletes_the_database(self):
        plan = rename_plan()
        self.assertEqual(plan[0].actions, [DELETE])
        self.assertTrue(naive_can_auto_apply(plan, plan_errors=[]))


class FixRenameDetection(unittest.TestCase):
    def test_rename_is_blocked_and_suggests_a_moved_block(self):
        review = review_plan(rename_plan())
        self.assertFalse(review.safe_to_auto_apply)
        self.assertEqual(review.findings[0].kind, "rename")
        self.assertIn("to = aws_db_instance.primary", review.findings[0].message)

    def test_computed_attributes_do_not_prevent_rename_matching(self):
        self.assertEqual(find_renames(rename_plan()),
                         {"aws_db_instance.main": "aws_db_instance.primary"})

    def test_delete_and_create_with_different_config_is_not_a_rename(self):
        plan = rename_plan()
        plan[1].after = {**DB_CONFIG, "allocated_storage": 100}
        review = review_plan(plan)
        self.assertEqual(find_renames(plan), {})
        self.assertEqual(review.findings[0].kind, "destroy")

    def test_each_create_matches_at_most_one_delete(self):
        plan = rename_plan()
        plan.insert(1, ResourceChange("aws_db_instance.replica", "aws_db_instance", [DELETE],
                                      before=db_attributes(id="orders-db-2")))
        renames = find_renames(plan)
        self.assertEqual(len(renames), 1)
        kinds = sorted(finding.kind for finding in review_plan(plan).findings)
        self.assertEqual(kinds, ["destroy", "rename"])


class FixReplaceAndDestroy(unittest.TestCase):
    def test_force_new_attribute_change_is_blocked_and_names_the_attribute(self):
        change = ResourceChange("aws_db_instance.main", "aws_db_instance", [DELETE, CREATE],
                                before=db_attributes(),
                                after={**DB_CONFIG, "engine": "aurora-postgresql"})
        finding = review_plan([change]).findings[0]
        self.assertEqual(finding.kind, "replace")
        self.assertIn("engine", finding.message)

    def test_create_before_destroy_order_is_also_a_replace(self):
        change = ResourceChange("aws_s3_bucket.invoices", "aws_s3_bucket", [CREATE, DELETE],
                                before={"bucket": "invoices"}, after={"bucket": "invoices-v2"})
        self.assertEqual(review_plan([change]).findings[0].kind, "replace")

    def test_stateless_resources_can_be_replaced_freely(self):
        change = ResourceChange("aws_instance.web", "aws_instance", [DELETE, CREATE],
                                before={"ami": "ami-old"}, after={"ami": "ami-new"})
        self.assertTrue(review_plan([change]).safe_to_auto_apply)

    def test_in_place_update_of_a_database_is_allowed(self):
        change = ResourceChange("aws_db_instance.main", "aws_db_instance", ["update"],
                                before=db_attributes(), after={**DB_CONFIG, "allocated_storage": 800})
        self.assertTrue(review_plan([change]).safe_to_auto_apply)

    def test_empty_plan_is_safe(self):
        self.assertTrue(review_plan([]).safe_to_auto_apply)

    def test_custom_stateful_types_are_respected(self):
        change = ResourceChange("aws_instance.web", "aws_instance", [DELETE],
                                before={"ami": "ami-old"})
        review = review_plan([change], stateful_types=frozenset({"aws_instance"}))
        self.assertEqual(review.findings[0].kind, "destroy")


if __name__ == "__main__":
    unittest.main()

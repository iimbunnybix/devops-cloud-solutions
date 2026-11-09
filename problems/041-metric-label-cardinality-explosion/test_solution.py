import random
import unittest

from solution import OVERFLOW_VALUE, GuardedRegistry, NaiveRegistry

METRIC = "http_requests_total"
ROUTES = [f"/api/v1/resource{i}" for i in range(10)]
METHODS = ["GET", "POST"]
STATUSES = ["200", "201", "400", "404", "500"]
USERS = 40_000
REQUESTS = 100_000
ALLOWED = {METRIC: {"route", "method", "status"}}


def request_labels(rng, with_user_id):
    """Labels of one simulated request; the release added user_id."""
    labels = {"route": rng.choice(ROUTES), "method": rng.choice(METHODS),
              "status": rng.choice(STATUSES)}
    if with_user_id:
        labels["user_id"] = f"u{rng.randint(1, USERS)}"
    return labels


def record_hour(registry, with_user_id=True, seed=42):
    """Feed one hour of seeded traffic into a registry."""
    rng = random.Random(seed)
    for _ in range(REQUESTS):
        registry.inc(METRIC, request_labels(rng, with_user_id))
    return registry


class ReproduceCardinalityExplosion(unittest.TestCase):
    def test_before_the_release_the_metric_has_100_series(self):
        self.assertEqual(record_hour(NaiveRegistry(), with_user_id=False).series_count(), 100)

    def test_user_id_label_creates_one_series_per_request_combination(self):
        series = record_hour(NaiveRegistry()).series_count()
        self.assertEqual(series, 98_757)
        self.assertGreater(series, 900 * 100)  # over 900x the 100 series before


class FixWithAllowListAndBudget(unittest.TestCase):
    def test_disallowed_user_id_label_is_dropped(self):
        registry = record_hour(GuardedRegistry(ALLOWED))
        self.assertEqual(registry.series_count(), 100)
        self.assertIn((METRIC, "user_id"), registry.dropped_label_names)

    def test_totals_are_unchanged_by_the_guard(self):
        self.assertEqual(record_hour(GuardedRegistry(ALLOWED)).total(METRIC), REQUESTS)

    def test_budget_caps_series_even_for_an_allowed_unbounded_label(self):
        allowed = {METRIC: {"route", "method", "status", "user_id"}}
        registry = record_hour(GuardedRegistry(allowed, max_series_per_metric=500))
        self.assertEqual(registry.series_count(), 500)
        self.assertEqual(registry.total(METRIC), REQUESTS)

    def test_samples_over_budget_go_to_the_overflow_series(self):
        allowed = {METRIC: {"route", "method", "status", "user_id"}}
        registry = record_hour(GuardedRegistry(allowed, max_series_per_metric=500))
        overflow_key = (METRIC, tuple(sorted((n, OVERFLOW_VALUE) for n in allowed[METRIC])))
        self.assertEqual(registry.values[overflow_key], registry.overflowed_samples[METRIC])
        self.assertEqual(registry.overflowed_samples[METRIC], 99_487)

    def test_existing_series_keep_counting_after_budget_is_full(self):
        registry = GuardedRegistry({"m": {"k"}}, max_series_per_metric=3)
        for value in ["a", "b", "c", "d", "a"]:
            registry.inc("m", {"k": value})
        self.assertEqual(registry.values[("m", (("k", "a"),))], 2)
        self.assertEqual(registry.values[("m", (("k", OVERFLOW_VALUE),))], 2)
        self.assertEqual(registry.series_count(), 3)

    def test_budgets_are_per_metric(self):
        registry = GuardedRegistry({"a": {"k"}, "b": {"k"}}, max_series_per_metric=2)
        for value in ["x", "y", "z"]:
            registry.inc("a", {"k": value})
        registry.inc("b", {"k": "x"})
        self.assertEqual(registry.values[("b", (("k", "x"),))], 1)

    def test_label_order_does_not_create_new_series(self):
        registry = GuardedRegistry({"m": {"a", "b"}})
        registry.inc("m", {"a": "1", "b": "2"})
        registry.inc("m", {"b": "2", "a": "1"})
        self.assertEqual(registry.series_count(), 1)

    def test_budget_below_one_is_rejected(self):
        with self.assertRaises(ValueError):
            GuardedRegistry({}, max_series_per_metric=0)


if __name__ == "__main__":
    unittest.main()

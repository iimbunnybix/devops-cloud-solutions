import random
import unittest

from solution import bucket_for, enabled_fraction, is_enabled, naive_is_enabled

FLAG = "new-checkout-ui"
ROLLOUT_PERCENT = 20
REQUESTS_PER_USER = 10


def make_users(count: int) -> list[str]:
    return [f"user-{index}" for index in range(count)]


def naive_flipping_users(user_ids: list[str], rng: random.Random) -> int:
    """Count users who saw both the old and new UI across their requests."""
    flipping = 0
    for _user_id in user_ids:
        answers = set()
        for _request in range(REQUESTS_PER_USER):
            answers.add(naive_is_enabled(ROLLOUT_PERCENT, rng))
        if len(answers) == 2:
            flipping += 1
    return flipping


class ReproduceProblem(unittest.TestCase):
    def test_most_users_flip_between_old_and_new_ui(self):
        users = make_users(10_000)
        flipping = naive_flipping_users(users, random.Random(42))
        # 1 - 0.8**10 - 0.2**10 is about 89% of users seeing both versions
        self.assertGreater(flipping / len(users), 0.85)


class FixStickyBucketing(unittest.TestCase):
    def test_same_user_gets_same_answer_on_every_request(self):
        for user_id in make_users(2_000):
            first = is_enabled(FLAG, user_id, ROLLOUT_PERCENT)
            for _request in range(REQUESTS_PER_USER):
                self.assertEqual(is_enabled(FLAG, user_id, ROLLOUT_PERCENT), first)

    def test_enabled_share_matches_rollout_percent(self):
        share = enabled_fraction(FLAG, make_users(100_000), ROLLOUT_PERCENT)
        self.assertAlmostEqual(share, 0.20, delta=0.005)

    def test_raising_percent_never_removes_a_user(self):
        users = make_users(20_000)
        previously_enabled: set[str] = set()
        for percent in (1, 5, 10, 25, 50, 100):
            now_enabled = {user for user in users if is_enabled(FLAG, user, percent)}
            self.assertTrue(previously_enabled <= now_enabled)
            previously_enabled = now_enabled

    def test_zero_percent_enables_nobody_and_hundred_enables_everyone(self):
        users = make_users(5_000)
        self.assertEqual(enabled_fraction(FLAG, users, 0), 0.0)
        self.assertEqual(enabled_fraction(FLAG, users, 100), 1.0)

    def test_fractional_percent_is_supported(self):
        share = enabled_fraction(FLAG, make_users(200_000), 0.5)
        self.assertAlmostEqual(share, 0.005, delta=0.001)

    def test_different_flags_pick_different_early_users(self):
        users = make_users(50_000)
        first_flag = {user for user in users if is_enabled("flag-a", user, 10)}
        second_flag = {user for user in users if is_enabled("flag-b", user, 10)}
        overlap = len(first_flag & second_flag) / len(users)
        # independent flags overlap on about 10% x 10% = 1% of users, not 10%
        self.assertAlmostEqual(overlap, 0.01, delta=0.003)

    def test_flag_and_user_boundary_does_not_collide(self):
        self.assertNotEqual(bucket_for("ab", "c"), bucket_for("a", "bc"))

    def test_invalid_percent_is_rejected(self):
        with self.assertRaises(ValueError):
            is_enabled(FLAG, "user-1", 120)
        with self.assertRaises(ValueError):
            is_enabled(FLAG, "user-1", -1)


if __name__ == "__main__":
    unittest.main()

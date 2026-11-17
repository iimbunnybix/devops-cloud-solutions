import unittest

from solution import (MIN_HEALTHY_SECONDS, Build, Deployment, NoRollbackTarget,
                      naive_rollback_target, rollback_target)

HOUR = 3600
NOW = 100 * HOUR


def builds() -> list[Build]:
    """CI built 1.4.0, then 1.4.1 (failed QA, never deployed), then 1.4.2."""
    return [Build("1.4.0", 1 * HOUR), Build("1.4.1", 2 * HOUR), Build("1.4.2", 3 * HOUR)]


def production_history() -> list[Deployment]:
    """1.3.9 and 1.4.0 ran for days; 1.4.2 is live now and broken."""
    return [
        Deployment("1.3.9", "prod", 10 * HOUR, True),
        Deployment("1.4.0", "prod", 50 * HOUR, True),
        Deployment("1.4.1", "staging", 60 * HOUR, False),
        Deployment("1.4.2", "prod", 99 * HOUR, True),
    ]


def deploy(version: str, at: float, rollback: bool = False, ok: bool = True) -> Deployment:
    return Deployment(version, "prod", at, ok, rollback)


class ReproduceProblem(unittest.TestCase):
    def test_rollback_ships_a_build_that_never_ran_in_production(self):
        self.assertEqual(naive_rollback_target(builds(), "1.4.2"), "1.4.1")

    def test_second_rollback_from_the_oldest_build_wraps_to_the_bad_release(self):
        # After rolling back to 1.4.0, the next rollback looks one build back
        # from it; index -1 wraps around in Python and picks broken 1.4.2.
        self.assertEqual(naive_rollback_target(builds(), "1.4.0"), "1.4.2")


class FixUsesDeployHistory(unittest.TestCase):
    def test_target_is_the_last_release_that_ran_in_production(self):
        self.assertEqual(rollback_target(production_history(), "prod", NOW), "1.4.0")

    def test_failed_and_other_environment_deploys_are_ignored(self):
        history = production_history() + [Deployment("1.4.3", "staging", 99.5 * HOUR, True)]
        self.assertEqual(rollback_target(history, "prod", NOW), "1.4.0")

    def test_second_rollback_skips_the_release_that_was_rolled_back(self):
        history = production_history() + [deploy("1.4.0", 99.5 * HOUR, rollback=True)]
        # 1.4.0 is live now; 1.4.2 is known bad, so the next target is 1.3.9.
        self.assertEqual(rollback_target(history, "prod", NOW), "1.3.9")

    def test_short_lived_release_is_not_trusted(self):
        history = [deploy("2.0.0", 0), deploy("2.0.1", 10 * HOUR),
                   deploy("2.0.2", 10 * HOUR + 300), deploy("2.0.3", 20 * HOUR)]
        # 2.0.1 was replaced after five minutes, so skip it and use 2.0.2.
        self.assertEqual(rollback_target(history, "prod", NOW), "2.0.2")

    def test_redeploy_of_the_same_version_is_not_a_target(self):
        history = [deploy("3.0.0", 0), deploy("3.1.0", 10 * HOUR), deploy("3.1.0", 20 * HOUR)]
        self.assertEqual(rollback_target(history, "prod", NOW), "3.0.0")

    def test_release_that_just_reached_the_health_window_counts(self):
        history = [deploy("4.0.0", 0), deploy("4.1.0", MIN_HEALTHY_SECONDS), deploy("4.2.0", NOW - 60)]
        self.assertEqual(rollback_target(history, "prod", NOW), "4.1.0")


class FixRefusesToGuess(unittest.TestCase):
    def test_no_history_raises(self):
        with self.assertRaises(NoRollbackTarget):
            rollback_target([], "prod", NOW)

    def test_only_one_release_ever_deployed_raises(self):
        with self.assertRaises(NoRollbackTarget):
            rollback_target([deploy("1.0.0", 0)], "prod", NOW)

    def test_every_earlier_release_bad_raises(self):
        history = [deploy("1.0.0", 0), deploy("1.1.0", 10 * HOUR),
                   deploy("1.0.0", 11 * HOUR, rollback=True)]
        # 1.1.0 was rolled back and 1.0.0 is current: nothing else to go to.
        with self.assertRaises(NoRollbackTarget):
            rollback_target(history, "prod", NOW)


if __name__ == "__main__":
    unittest.main()

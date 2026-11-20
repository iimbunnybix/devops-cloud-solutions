import random
import unittest

from solution import (
    DOWN,
    UP,
    NaiveChecker,
    QuorumChecker,
    count_pages_naive,
    count_pages_quorum,
)

LOCATIONS = ["ap-southeast", "eu-central", "eu-west", "us-east", "us-west"]
NAIVE_LOCATION = "us-east"
ROUNDS_PER_MONTH = 30 * 24 * 60   # one probe round per minute
BLIP_START_PROBABILITY = 0.001    # per location per minute
MAX_BLIP_MINUTES = 5
SEED = 1


def all_up() -> dict[str, bool]:
    return {location: True for location in LOCATIONS}


def failing(*locations: str) -> dict[str, bool]:
    probes = all_up()
    for location in locations:
        probes[location] = False
    return probes


def simulated_month(outage: range = range(0)) -> list[dict[str, bool]]:
    """Independent short network blips per location, plus an optional real outage."""
    rng = random.Random(SEED)
    blip_minutes_left = {location: 0 for location in LOCATIONS}
    rounds = []
    for minute in range(ROUNDS_PER_MONTH):
        probes = {}
        for location in LOCATIONS:
            if blip_minutes_left[location] == 0 and rng.random() < BLIP_START_PROBABILITY:
                blip_minutes_left[location] = rng.randint(1, MAX_BLIP_MINUTES)
            probes[location] = blip_minutes_left[location] == 0 and minute not in outage
            if blip_minutes_left[location]:
                blip_minutes_left[location] -= 1
        rounds.append(probes)
    return rounds


class ReproduceProblem(unittest.TestCase):
    def test_one_failed_probe_from_one_location_pages(self):
        checker = NaiveChecker(NAIVE_LOCATION)
        self.assertTrue(checker.observe(failing(NAIVE_LOCATION)))
        self.assertEqual(checker.state, DOWN)

    def test_a_month_of_regional_blips_sends_dozens_of_false_pages(self):
        pages = count_pages_naive(NaiveChecker(NAIVE_LOCATION), simulated_month())
        self.assertEqual(pages, 42)

    def test_naive_monitor_ignores_an_outage_its_location_cannot_see(self):
        checker = NaiveChecker(NAIVE_LOCATION)
        self.assertFalse(checker.observe(failing("eu-west", "eu-central", "ap-southeast", "us-west")))


class FixRequiresQuorum(unittest.TestCase):
    def test_single_location_failure_does_not_page(self):
        checker = QuorumChecker(LOCATIONS)
        for _ in range(10):
            self.assertFalse(checker.observe(failing(NAIVE_LOCATION)).pages)
        self.assertEqual(checker.state, UP)

    def test_two_location_regional_blip_does_not_page(self):
        checker = QuorumChecker(LOCATIONS)
        for _ in range(10):
            checker.observe(failing("eu-west", "eu-central"))
        self.assertEqual(checker.state, UP)

    def test_the_same_blippy_month_sends_no_pages(self):
        self.assertEqual(count_pages_quorum(QuorumChecker(LOCATIONS), simulated_month()), 0)

    def test_missing_probe_results_are_not_counted_as_failures(self):
        checker = QuorumChecker(LOCATIONS)
        for _ in range(3):
            checker.observe({"us-east": False, "us-west": False})
        self.assertEqual(checker.state, UP)


class FixRequiresConsecutiveRounds(unittest.TestCase):
    def test_one_round_of_quorum_failure_does_not_page(self):
        checker = QuorumChecker(LOCATIONS)
        self.assertFalse(checker.observe(failing(*LOCATIONS)).pages)
        self.assertFalse(checker.observe(all_up()).pages)
        self.assertEqual(checker.state, UP)

    def test_quorum_failing_for_two_rounds_pages_once(self):
        checker = QuorumChecker(LOCATIONS)
        verdicts = [checker.observe(failing("eu-west", "us-east", "us-west")) for _ in range(5)]
        self.assertEqual([verdict.pages for verdict in verdicts], [False, True, False, False, False])

    def test_real_outage_in_a_blippy_month_pages_exactly_once(self):
        rounds = simulated_month(outage=range(1000, 1010))
        self.assertEqual(count_pages_quorum(QuorumChecker(LOCATIONS), rounds), 1)

    def test_real_outage_pages_in_its_second_minute(self):
        rounds = simulated_month(outage=range(1000, 1010))
        checker = QuorumChecker(LOCATIONS)
        paged_at = [minute for minute, probes in enumerate(rounds) if checker.observe(probes).pages]
        self.assertEqual(paged_at, [1001])

    def test_recovery_needs_consecutive_healthy_rounds(self):
        checker = QuorumChecker(LOCATIONS)
        for _ in range(2):
            checker.observe(failing(*LOCATIONS))
        self.assertEqual(checker.observe(all_up()).state, DOWN)
        self.assertEqual(checker.observe(failing(*LOCATIONS)).state, DOWN)
        self.assertEqual(checker.observe(all_up()).state, DOWN)
        verdict = checker.observe(all_up())
        self.assertEqual(verdict.state, UP)
        self.assertTrue(verdict.changed)


class FixReportsLocations(unittest.TestCase):
    def test_verdict_names_the_failed_locations(self):
        verdict = QuorumChecker(LOCATIONS).observe(failing("us-west", "eu-west"))
        self.assertEqual(verdict.failed_locations, ["eu-west", "us-west"])

    def test_unknown_location_in_results_is_ignored(self):
        probes = failing("us-west")
        probes["sa-east"] = False
        self.assertEqual(QuorumChecker(LOCATIONS).observe(probes).failed_locations, ["us-west"])


class FixValidatesSettings(unittest.TestCase):
    def test_quorum_larger_than_location_count_is_rejected(self):
        with self.assertRaises(ValueError):
            QuorumChecker(LOCATIONS, quorum=6)

    def test_zero_quorum_is_rejected(self):
        with self.assertRaises(ValueError):
            QuorumChecker(LOCATIONS, quorum=0)

    def test_zero_failing_rounds_is_rejected(self):
        with self.assertRaises(ValueError):
            QuorumChecker(LOCATIONS, failing_rounds_to_alert=0)


if __name__ == "__main__":
    unittest.main()

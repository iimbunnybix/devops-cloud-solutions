import unittest

from solution import latest_release, naive_latest, parse_tag, previous_release, sorted_releases

RELEASE_TAGS = ["v1.8.2", "v1.9.0", "v1.9.1", "v1.10.0", "v1.10.1"]


class ReproduceProblem(unittest.TestCase):
    def test_string_max_picks_an_older_release(self):
        self.assertEqual(naive_latest(RELEASE_TAGS), "v1.9.1")

    def test_string_order_puts_1_10_before_1_9(self):
        self.assertLess("v1.10.0", "v1.9.0")


class FixSemverOrder(unittest.TestCase):
    def test_latest_is_the_highest_numeric_version(self):
        self.assertEqual(latest_release(RELEASE_TAGS), "v1.10.1")

    def test_rollback_goes_to_the_release_just_before(self):
        self.assertEqual(previous_release(RELEASE_TAGS, "v1.10.0"), "v1.9.1")

    def test_major_version_with_more_digits(self):
        self.assertEqual(latest_release(["v9.0.0", "v10.0.0"]), "v10.0.0")

    def test_prereleases_are_not_deployed_as_latest(self):
        self.assertEqual(latest_release(RELEASE_TAGS + ["v1.11.0-rc.1"]), "v1.10.1")

    def test_release_sorts_after_its_prereleases(self):
        tags = ["v2.0.0", "v2.0.0-rc.10", "v2.0.0-rc.2", "v2.0.0-beta"]
        ordered = sorted_releases(tags, include_prereleases=True)
        self.assertEqual(ordered, ["v2.0.0-beta", "v2.0.0-rc.2", "v2.0.0-rc.10", "v2.0.0"])

    def test_numeric_prerelease_parts_sort_before_text_parts(self):
        ordered = sorted_releases(["v1.0.0-alpha", "v1.0.0-1"], include_prereleases=True)
        self.assertEqual(ordered, ["v1.0.0-1", "v1.0.0-alpha"])

    def test_junk_tags_are_ignored(self):
        tags = RELEASE_TAGS + ["latest", "v1.99", "nightly-2026-11-13", "v2.0.0.1", "v3.0.0\n"]
        self.assertEqual(latest_release(tags), "v1.10.1")

    def test_no_valid_tags_raises(self):
        with self.assertRaises(ValueError):
            latest_release(["latest", "main"])

    def test_rollback_from_oldest_release_raises(self):
        with self.assertRaises(ValueError):
            previous_release(RELEASE_TAGS, "v1.8.2")

    def test_parse_extracts_numbers(self):
        parsed = parse_tag("v1.10.0-rc.2")
        self.assertEqual((parsed.major, parsed.minor, parsed.patch, parsed.prerelease), (1, 10, 0, ("rc", "2")))


if __name__ == "__main__":
    unittest.main()

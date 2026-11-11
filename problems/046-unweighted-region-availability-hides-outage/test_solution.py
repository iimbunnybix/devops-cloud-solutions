import unittest

from solution import (RegionStats, availability_report, fleet_availability,
                      naive_fleet_availability, region_availability)

MILLION = 1_000_000


def outage_in_busiest_region() -> list[RegionStats]:
    """us-east serves most traffic and fails 40% of it; three small regions are fine."""
    return [
        RegionStats("us-east-1", good_requests=6 * MILLION, total_requests=10 * MILLION),
        RegionStats("eu-west-1", good_requests=999_900, total_requests=MILLION),
        RegionStats("ap-south-1", good_requests=499_950, total_requests=500_000),
        RegionStats("sa-east-1", good_requests=99_990, total_requests=100_000),
    ]


def blip_in_tiny_region() -> list[RegionStats]:
    """A new region with 2,000 requests has 100 errors; the big regions are perfect."""
    return [
        RegionStats("us-east-1", good_requests=10 * MILLION, total_requests=10 * MILLION),
        RegionStats("eu-west-1", good_requests=MILLION, total_requests=MILLION),
        RegionStats("me-central-1", good_requests=1_900, total_requests=2_000),
    ]


class ReproduceProblem(unittest.TestCase):
    def test_averaging_hides_most_of_a_big_region_outage(self):
        # 40% of us-east requests fail, yet the dashboard shows 89.99%.
        self.assertAlmostEqual(naive_fleet_availability(outage_in_busiest_region()), 0.899925, places=6)

    def test_averaging_exaggerates_a_tiny_region_blip(self):
        # 100 failed requests out of 11 million show up as a 1.7 point drop.
        self.assertAlmostEqual(naive_fleet_availability(blip_in_tiny_region()), 0.98333, places=5)


class FixTrafficWeightedAvailability(unittest.TestCase):
    def test_big_region_outage_shows_its_true_impact(self):
        self.assertAlmostEqual(fleet_availability(outage_in_busiest_region()), 0.65516, places=5)

    def test_tiny_region_blip_barely_moves_the_fleet_number(self):
        self.assertAlmostEqual(fleet_availability(blip_in_tiny_region()), 0.99999, places=5)

    def test_region_with_no_traffic_does_not_count_as_perfect(self):
        regions = [RegionStats("a", 90, 100), RegionStats("b", 0, 0)]
        self.assertAlmostEqual(fleet_availability(regions), 0.90)
        self.assertIsNone(region_availability(regions[1]))

    def test_no_traffic_anywhere_is_unknown(self):
        self.assertIsNone(fleet_availability([RegionStats("a", 0, 0)]))

    def test_more_good_than_total_is_rejected(self):
        with self.assertRaises(ValueError):
            fleet_availability([RegionStats("a", 101, 100)])


class FixPerRegionBreaches(unittest.TestCase):
    def test_tiny_region_breach_is_reported_even_if_fleet_is_fine(self):
        report = availability_report(blip_in_tiny_region())
        self.assertGreater(report.fleet_availability, 0.999)
        self.assertEqual(report.regions_breaching_slo, ["me-central-1"])
        self.assertFalse(report.meets_slo)

    def test_breaching_regions_are_listed_worst_first(self):
        regions = [RegionStats("a", 995, 1000), RegionStats("b", 900, 1000), RegionStats("c", 1000, 1000)]
        self.assertEqual(availability_report(regions).regions_breaching_slo, ["b", "a"])

    def test_region_exactly_at_target_is_not_a_breach(self):
        report = availability_report([RegionStats("a", 999, 1000)])
        self.assertEqual(report.regions_breaching_slo, [])
        self.assertTrue(report.meets_slo)

    def test_silent_region_is_listed_separately(self):
        report = availability_report([RegionStats("a", 1000, 1000), RegionStats("b", 0, 0)])
        self.assertEqual(report.regions_without_traffic, ["b"])


if __name__ == "__main__":
    unittest.main()

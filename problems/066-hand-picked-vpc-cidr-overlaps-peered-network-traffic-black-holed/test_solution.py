import ipaddress
import unittest

from solution import AllocationError, UsedRange, allocate, find_overlaps, naive_accept, reserve, validate

SUPERNET = "10.0.0.0/8"


def net(text):
    return ipaddress.IPv4Network(text)


def company_ranges():
    """What is already routed: two own VPCs, the office VPN and a partner peer."""
    return [
        UsedRange(net("10.0.0.0/16"), "vpc:payments"),
        UsedRange(net("10.1.0.0/16"), "vpc:checkout"),
        UsedRange(net("10.20.8.0/21"), "vpn:office"),
        UsedRange(net("10.20.64.0/18"), "peer:partner"),
    ]


class ReproduceProblem(unittest.TestCase):
    def test_equality_check_accepts_a_range_that_swallows_the_vpn_and_partner(self):
        self.assertTrue(naive_accept("10.20.0.0/16", company_ranges()))

    def test_the_accepted_range_really_overlaps_two_networks(self):
        owners = [used.owner for used in find_overlaps(net("10.20.0.0/16"), company_ranges())]
        self.assertEqual(owners, ["vpn:office", "peer:partner"])


class FixValidate(unittest.TestCase):
    def test_proposed_range_containing_others_is_reported(self):
        problems = validate("10.20.0.0/16", company_ranges())
        self.assertEqual(problems, [
            "10.20.0.0/16 overlaps 10.20.8.0/21 (vpn:office)",
            "10.20.0.0/16 overlaps 10.20.64.0/18 (peer:partner)",
        ])

    def test_proposed_range_inside_an_existing_one_is_reported(self):
        self.assertEqual(len(validate("10.0.5.0/24", company_ranges())), 1)

    def test_adjacent_range_is_not_an_overlap(self):
        self.assertEqual(validate("10.2.0.0/16", company_ranges()), [])

    def test_cidr_with_host_bits_set_is_rejected(self):
        with self.assertRaises(ValueError):
            validate("10.20.1.0/16", company_ranges())


class FixAllocate(unittest.TestCase):
    def test_first_free_slash_16_skips_used_vpcs(self):
        self.assertEqual(allocate(SUPERNET, 16, company_ranges()), net("10.2.0.0/16"))

    def test_allocated_block_overlaps_nothing(self):
        block = allocate(SUPERNET, 16, company_ranges())
        self.assertEqual(find_overlaps(block, company_ranges()), [])

    def test_small_block_fills_a_gap_next_to_the_vpn(self):
        used = company_ranges()
        self.assertEqual(allocate("10.20.0.0/16", 21, used), net("10.20.0.0/21"))
        used.append(UsedRange(net("10.20.0.0/21"), "vpc:analytics"))
        self.assertEqual(allocate("10.20.0.0/16", 21, used), net("10.20.16.0/21"))

    def test_reserve_records_the_block_so_the_next_one_differs(self):
        used = company_ranges()
        first = reserve(SUPERNET, 16, "vpc:analytics", used)
        second = reserve(SUPERNET, 16, "vpc:search", used)
        self.assertEqual((first, second), (net("10.2.0.0/16"), net("10.3.0.0/16")))

    def test_full_supernet_raises(self):
        used = [UsedRange(net("192.168.0.0/24"), "vpc:lab")]
        with self.assertRaises(AllocationError):
            allocate("192.168.0.0/24", 26, used)

    def test_block_larger_than_supernet_is_rejected(self):
        with self.assertRaises(ValueError):
            allocate("10.20.0.0/16", 8, [])


if __name__ == "__main__":
    unittest.main()

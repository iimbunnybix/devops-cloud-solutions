import unittest

from solution import (DbClient, DnsError, FakeClock, FakeDns, FakeNetwork, ForeverResolver,
                      TtlResolver, run_traffic)

NAME = "db.prod.internal"
OLD, NEW = "10.0.1.10", "10.0.2.20"
TTL = 30
DNS_LAG = 10          # seconds between the old primary dying and DNS pointing at the new one
WINDOW = 600          # observe 10 minutes after the failover


def scenario(make_client):
    """Warm up, fail over, return (failures after failover, dns queries during window)."""
    clock, dns, net = FakeClock(), FakeDns(), FakeNetwork({OLD})
    dns.set(NAME, OLD, TTL)
    client = make_client(dns, clock, net)
    assert run_traffic(client, clock, 95) == 0          # healthy; failover lands mid-TTL
    net.up = {NEW}                                      # old primary dies
    queries_before = dns.queries
    failures = run_traffic(client, clock, DNS_LAG)
    dns.set(NAME, NEW, TTL)                             # failover finishes in DNS
    failures += run_traffic(client, clock, WINDOW - DNS_LAG)
    return failures, dns.queries - queries_before


def forever(dns, clock, net):
    return DbClient(NAME, ForeverResolver(dns), net)


def ttl_only(dns, clock, net):
    return DbClient(NAME, TtlResolver(dns, clock), net, invalidate_on_failure=False)


def ttl_invalidate_retry(dns, clock, net):
    return DbClient(NAME, TtlResolver(dns, clock), net)


class ReproducePinnedIp(unittest.TestCase):
    def test_app_never_follows_the_failover(self):
        failures, queries = scenario(forever)
        self.assertEqual(failures, WINDOW)          # every attempt for 10 minutes fails
        self.assertEqual(queries, 0)                # it never even asks DNS again


class TtlAwareResolver(unittest.TestCase):
    def test_ttl_alone_bounds_the_outage(self):
        failures, _ = scenario(ttl_only)
        self.assertEqual(failures, 25)              # until the cached answer expires

    def test_invalidate_and_retry_recovers_as_soon_as_dns_does(self):
        failures, queries = scenario(ttl_invalidate_retry)
        self.assertEqual(failures, DNS_LAG)         # only while DNS still pointed at the old IP
        self.assertEqual(queries, 30)              # 1/s while broken (rate-limited), then 1 per TTL

    def test_ttl_clamped(self):
        clock, dns = FakeClock(), FakeDns()
        dns.set(NAME, OLD, 86_400)                  # someone set a 1-day TTL
        r = TtlResolver(dns, clock, max_ttl=60)
        r.get(NAME)
        dns.set(NAME, NEW, 86_400)
        clock.advance(61)
        self.assertEqual(r.get(NAME), NEW)

    def test_zero_ttl_does_not_query_every_request(self):
        clock, dns = FakeClock(), FakeDns()
        dns.set(NAME, OLD, 0)
        r = TtlResolver(dns, clock, min_ttl=1.0)
        for _ in range(100):
            r.get(NAME)
            clock.advance(0.01)
        self.assertEqual(dns.queries, 1)

    def test_serves_stale_when_dns_is_down(self):
        clock, dns = FakeClock(), FakeDns()
        dns.set(NAME, OLD, TTL)
        r = TtlResolver(dns, clock, stale_grace=300)
        r.get(NAME)
        dns.down = True
        clock.advance(TTL + 200)
        self.assertEqual(r.get(NAME), OLD)          # within grace
        clock.advance(200)
        with self.assertRaises(DnsError):           # grace exhausted
            r.get(NAME)

    def test_unknown_name_raises(self):
        with self.assertRaises(DnsError):
            TtlResolver(FakeDns(), FakeClock()).get("nope.internal")

    def test_same_ip_after_invalidate_still_fails_cleanly(self):
        clock, dns, net = FakeClock(), FakeDns(), FakeNetwork(set())
        dns.set(NAME, OLD, TTL)
        client = DbClient(NAME, TtlResolver(dns, clock), net)
        with self.assertRaises(ConnectionError):
            client.connect()


if __name__ == "__main__":
    unittest.main()

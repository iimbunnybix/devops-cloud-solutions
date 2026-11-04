import unittest

from solution import DAY, CertMonitor, format_not_after, not_after_epoch

NOW = 1_793_000_000.0   # fixed fake "now" (Oct 2026)


def cert(cn, days_left, serial="01"):
    return {"subject": ((("commonName", cn),),), "serialNumber": serial,
            "notAfter": format_not_after(NOW + days_left * DAY)}


def chain(leaf_days, inter_days=900, serial="01"):
    return [cert("api.example.test", leaf_days, serial), cert("Example Intermediate CA", inter_days, "I1")]


class FakeFetcher:
    def __init__(self, table):
        self.table = table          # (host, addr) -> chain or Exception

    def __call__(self, host, addr):
        v = self.table[(host, addr)]
        if isinstance(v, Exception):
            raise v
        return v


def kinds(findings):
    return sorted(f.kind for f in findings)


def monitor(table):
    return CertMonitor(FakeFetcher(table), clock=lambda: NOW)


class OldCheckMissedIt(unittest.TestCase):
    def test_leaf_only_single_ip_check_reports_healthy(self):
        """The previous check: first IP, leaf cert, alert only at <= 7 days."""
        table = {("api", "10.0.0.1"): chain(60), ("api", "10.0.0.2"): chain(3, serial="00")}
        leaf = table[("api", "10.0.0.1")][0]
        days_left = (not_after_epoch(leaf) - NOW) / DAY
        self.assertGreater(days_left, 7)              # old check: "all good"
        f = monitor(table).check({"api": ["10.0.0.1", "10.0.0.2"]})
        self.assertIn("CRITICAL", kinds(f))           # new check: node .2 is 3 days out
        self.assertIn("INCONSISTENT", kinds(f))


class Thresholds(unittest.TestCase):
    def check_one(self, leaf_days, inter_days=900):
        return monitor({("h", "a"): chain(leaf_days, inter_days)}).check({"h": ["a"]})

    def test_healthy_cert_has_no_findings(self):
        self.assertEqual(self.check_one(60), [])

    def test_renewal_window_without_alert_noise(self):
        self.assertEqual(self.check_one(29), [])      # inside 2-day grace

    def test_renewal_overdue_is_early_ticket(self):
        f = self.check_one(25)
        self.assertEqual(kinds(f), ["RENEWAL_OVERDUE"])
        self.assertEqual(f[0].severity, "ticket")

    def test_warning_and_critical(self):
        self.assertEqual(kinds(self.check_one(14)), ["RENEWAL_OVERDUE", "WARNING"])
        crit = [x for x in self.check_one(5) if x.kind == "CRITICAL"]
        self.assertEqual(crit[0].severity, "page")

    def test_expired(self):
        f = self.check_one(-2)
        self.assertEqual(kinds(f), ["EXPIRED"])
        self.assertAlmostEqual(f[0].days_left, -2, places=3)

    def test_boundary_exactly_page_days(self):
        self.assertIn("CRITICAL", kinds(self.check_one(7)))

    def test_expiring_intermediate_is_caught_even_with_fresh_leaf(self):
        f = self.check_one(80, inter_days=4)
        self.assertEqual(kinds(f), ["CRITICAL", "RENEWAL_OVERDUE"])
        self.assertIn("chain[1]", f[0].detail)


class FleetChecks(unittest.TestCase):
    def test_unreachable_backend_is_reported_not_skipped(self):
        table = {("h", "a"): chain(60), ("h", "b"): ConnectionRefusedError("refused"),
                 ("h", "c"): []}
        f = monitor(table).check({"h": ["a", "b", "c"]})
        self.assertEqual(kinds(f), ["UNREACHABLE", "UNREACHABLE"])

    def test_consistent_fleet_is_quiet(self):
        table = {("h", ip): chain(60) for ip in ("a", "b", "c")}
        self.assertEqual(monitor(table).check({"h": ["a", "b", "c"]}), [])

    def test_multiple_hosts_are_independent(self):
        table = {("x", "a"): chain(60), ("y", "a"): chain(-1)}
        f = monitor(table).check({"x": ["a"], "y": ["a"]})
        self.assertEqual([(i.host, i.kind) for i in f], [("y", "EXPIRED")])


class Parsing(unittest.TestCase):
    def test_round_trip_openssl_format(self):
        s = format_not_after(NOW)
        self.assertRegex(s, r"^[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d \d{4} GMT$")
        self.assertEqual(not_after_epoch({"notAfter": s}), NOW)

    def test_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            CertMonitor(lambda h, a: [], warn_days=5, page_days=7)


if __name__ == "__main__":
    unittest.main()

import unittest

from solution import (HOUR, MAX_SILENCE_SECONDS, Alert, SilenceRejected, create_silence,
                      naive_create_silence, route_alerts)

WINDOW_START = 2 * HOUR            # Saturday 02:00, planned Postgres upgrade
WINDOW_END = 4 * HOUR              # Saturday 04:00
REGION = {"env": "prod", "region": "eu-west-1"}
DB_SCOPE = {"service": "payments-db", "cluster": "pg-eu-1"}


def prod_alert(name: str, service: str, cluster: str, fired_at: float, **extra: str) -> Alert:
    labels = {**REGION, "service": service, "cluster": cluster, **extra}
    return Alert(name, labels, fired_at)


def maintenance_night() -> list[Alert]:
    """The alerts that fired during and after the database maintenance."""
    return [
        prod_alert("PostgresDown", "payments-db", "pg-eu-1", 2 * HOUR + 10 * 60),
        prod_alert("ReplicationLagHigh", "payments-db", "pg-eu-1", 2 * HOUR + 30 * 60),
        prod_alert("Checkout5xxRateHigh", "checkout-api", "k8s-eu-1", 2 * HOUR + 40 * 60),
        prod_alert("PostgresDown", "payments-db", "pg-eu-1", 5 * HOUR),   # after the window: real
        prod_alert("DiskWillFillIn4h", "search", "es-eu-2", 55 * HOUR),   # Monday 07:00
    ]


def scoped_db_silence():
    return create_silence(DB_SCOPE, WINDOW_START, WINDOW_END, "asha", "PG 15 -> 16 upgrade")


class ReproduceProblem(unittest.TestCase):
    def setUp(self):
        broad = naive_create_silence(REGION, WINDOW_START, None, "asha", "db maintenance")
        self.result = route_alerts(maintenance_night(), [broad])

    def test_region_wide_silence_hides_unrelated_checkout_outage(self):
        self.assertIn("Checkout5xxRateHigh", self.result.silenced_names())

    def test_silence_without_end_keeps_hiding_alerts_days_later(self):
        self.assertIn("DiskWillFillIn4h", self.result.silenced_names())

    def test_on_call_receives_nothing_at_all(self):
        self.assertEqual(self.result.delivered_names(), [])


class FixRejectsUnsafeSilences(unittest.TestCase):
    def test_region_only_matcher_is_rejected(self):
        with self.assertRaises(SilenceRejected):
            create_silence(REGION, WINDOW_START, WINDOW_END, "asha", "db maintenance")

    def test_service_without_cluster_or_instance_is_rejected(self):
        with self.assertRaises(SilenceRejected):
            create_silence({"service": "payments-db"}, WINDOW_START, WINDOW_END, "asha", "upgrade")

    def test_silence_without_end_time_is_rejected(self):
        with self.assertRaises(SilenceRejected):
            create_silence(DB_SCOPE, WINDOW_START, None, "asha", "upgrade")

    def test_silence_longer_than_the_limit_is_rejected(self):
        with self.assertRaises(SilenceRejected):
            create_silence(DB_SCOPE, 0, MAX_SILENCE_SECONDS + 1, "asha", "upgrade")

    def test_reversed_window_is_rejected(self):
        with self.assertRaises(SilenceRejected):
            create_silence(DB_SCOPE, WINDOW_END, WINDOW_START, "asha", "upgrade")

    def test_silence_without_owner_or_comment_is_rejected(self):
        with self.assertRaises(SilenceRejected):
            create_silence(DB_SCOPE, WINDOW_START, WINDOW_END, "", "")

    def test_limit_length_window_is_accepted(self):
        silence = create_silence(DB_SCOPE, 0, MAX_SILENCE_SECONDS, "asha", "upgrade")
        self.assertEqual(silence.ends_at, MAX_SILENCE_SECONDS)


class FixScopedSilence(unittest.TestCase):
    def setUp(self):
        self.result = route_alerts(maintenance_night(), [scoped_db_silence()])

    def test_only_expected_maintenance_alerts_are_silenced(self):
        self.assertEqual(self.result.silenced_names(), ["PostgresDown", "ReplicationLagHigh"])

    def test_unrelated_outage_and_later_alerts_reach_on_call(self):
        self.assertEqual(self.result.delivered_names(),
                         ["Checkout5xxRateHigh", "PostgresDown", "DiskWillFillIn4h"])

    def test_alert_at_the_exact_end_time_is_delivered(self):
        at_end = prod_alert("PostgresDown", "payments-db", "pg-eu-1", WINDOW_END)
        self.assertEqual(route_alerts([at_end], [scoped_db_silence()]).delivered_names(),
                         ["PostgresDown"])

    def test_silence_does_not_apply_before_it_starts(self):
        early = prod_alert("PostgresDown", "payments-db", "pg-eu-1", WINDOW_START - 1)
        self.assertEqual(route_alerts([early], [scoped_db_silence()]).delivered_names(),
                         ["PostgresDown"])

    def test_exempt_alert_is_never_silenced(self):
        exempt = prod_alert("WalArchivingStopped", "payments-db", "pg-eu-1", 3 * HOUR,
                            silence_exempt="true")
        self.assertEqual(route_alerts([exempt], [scoped_db_silence()]).delivered_names(),
                         ["WalArchivingStopped"])

    def test_silenced_alerts_record_which_silence_hid_them(self):
        _, silence = self.result.silenced[0]
        self.assertEqual(silence.created_by, "asha")


if __name__ == "__main__":
    unittest.main()

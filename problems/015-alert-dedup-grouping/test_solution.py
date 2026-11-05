import unittest

from solution import (GROUP_INTERVAL_S, AlertGrouper, fingerprint,
                      naive_notifications)

EVALUATION_INTERVAL_S = 60
OUTAGE_MINUTES = 10
APP_HOSTS = 30


def outage_firings() -> list[tuple[float, dict[str, str]]]:
    """The incident: every app host and the database alert once per minute."""
    firings = []
    for minute in range(OUTAGE_MINUTES):
        now = float(minute * EVALUATION_INTERVAL_S)
        firings.append((now, {"alertname": "PostgresDown", "cluster": "prod-eu",
                              "instance": "db-1"}))
        for host in range(APP_HOSTS):
            firings.append((now, {"alertname": "DBConnectionErrors",
                                  "cluster": "prod-eu", "instance": f"app-{host}"}))
    return firings


def run_grouper(grouper: AlertGrouper, firings, end_s: float, tick_s: float = 10.0):
    """Feed firings in time order and flush on a fake clock every ``tick_s``."""
    pending = sorted(firings, key=lambda item: item[0])
    now = 0.0
    while now <= end_s:
        while pending and pending[0][0] <= now:
            grouper.receive(pending.pop(0)[1], now)
        grouper.flush(now)
        now += tick_s
    return grouper.sent


class ReproduceProblem(unittest.TestCase):
    def test_naive_pager_sends_one_page_per_firing(self):
        pages = naive_notifications(outage_firings())
        self.assertEqual(len(pages), 310)


class FixFingerprint(unittest.TestCase):
    def test_label_order_does_not_change_fingerprint(self):
        self.assertEqual(fingerprint({"a": "1", "b": "2"}),
                         fingerprint({"b": "2", "a": "1"}))

    def test_different_instance_gives_different_fingerprint(self):
        self.assertNotEqual(fingerprint({"instance": "app-1"}),
                            fingerprint({"instance": "app-2"}))


class FixGrouping(unittest.TestCase):
    def test_outage_produces_one_page_per_alertname(self):
        sent = run_grouper(AlertGrouper(), outage_firings(), end_s=OUTAGE_MINUTES * 60)
        self.assertEqual(len(sent), 2)

    def test_one_page_lists_every_affected_host(self):
        sent = run_grouper(AlertGrouper(), outage_firings(), end_s=OUTAGE_MINUTES * 60)
        sizes = sorted(len(page.alert_fingerprints) for page in sent)
        self.assertEqual(sizes, [1, APP_HOSTS])

    def test_nothing_is_sent_before_group_wait(self):
        grouper = AlertGrouper(group_wait_s=30)
        grouper.receive({"alertname": "X", "cluster": "c"}, now=0)
        self.assertEqual(grouper.flush(now=29), [])
        self.assertEqual(len(grouper.flush(now=30)), 1)

    def test_duplicate_firings_are_merged(self):
        grouper = AlertGrouper(group_wait_s=0)
        for _ in range(5):
            grouper.receive({"alertname": "X", "cluster": "c", "instance": "a"}, now=0)
        sent = grouper.flush(now=0)
        self.assertEqual(len(sent[0].alert_fingerprints), 1)

    def test_different_clusters_page_separately(self):
        grouper = AlertGrouper(group_wait_s=0)
        grouper.receive({"alertname": "X", "cluster": "eu"}, now=0)
        grouper.receive({"alertname": "X", "cluster": "us"}, now=0)
        self.assertEqual(len(grouper.flush(now=0)), 2)


class FixFollowUps(unittest.TestCase):
    def setUp(self) -> None:
        self.grouper = AlertGrouper(group_wait_s=0, group_interval_s=300,
                                    repeat_interval_s=3600)
        self.grouper.receive({"alertname": "X", "cluster": "c", "instance": "a"}, now=0)
        self.grouper.flush(now=0)

    def test_new_member_waits_for_group_interval(self):
        self.grouper.receive({"alertname": "X", "cluster": "c", "instance": "b"}, now=10)
        self.assertEqual(self.grouper.flush(now=299), [])
        update = self.grouper.flush(now=GROUP_INTERVAL_S)
        self.assertEqual(len(update[0].alert_fingerprints), 2)

    def test_unchanged_group_is_not_resent_before_repeat_interval(self):
        self.assertEqual(self.grouper.flush(now=3599), [])
        self.assertEqual(len(self.grouper.flush(now=3600)), 1)

    def test_resolved_group_stops_paging(self):
        self.grouper.resolve({"alertname": "X", "cluster": "c", "instance": "a"})
        self.assertEqual(self.grouper.flush(now=7200), [])

    def test_resolving_unknown_alert_is_ignored(self):
        self.grouper.resolve({"alertname": "Nope", "cluster": "c"})
        self.assertEqual(len(self.grouper.groups), 1)


if __name__ == "__main__":
    unittest.main()

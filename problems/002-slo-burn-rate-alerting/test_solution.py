import unittest

from solution import BurnRateAlerter, static_threshold_alert

RPM = 2000                     # requests per minute


def healthy(minutes):
    return [(RPM, 1)] * minutes            # 0.05% errors, inside the SLO


class BurnRateTests(unittest.TestCase):
    def replay(self, traffic):
        a = BurnRateAlerter()
        return [a.observe_minute(r, e) for r, e in traffic]

    def test_short_blip_does_not_page(self):
        traffic = healthy(120) + [(RPM, 400)] * 2 + healthy(60)       # 20% errors for 2 min
        pages = [m for m in self.replay(traffic) if "page" in m]
        self.assertEqual(pages, [])

    def test_old_static_rule_would_have_paged_on_the_blip(self):
        traffic = healthy(120) + [(RPM, 400)] * 2
        self.assertTrue(static_threshold_alert(traffic))

    def first_page_minute(self, error_rate, minutes=60):
        traffic = healthy(120) + [(RPM, int(RPM * error_rate))] * minutes
        result = self.replay(traffic)
        return next(i for i, m in enumerate(result) if "page" in m) - 120 + 1

    def test_the_missed_40_minute_outage_now_pages(self):
        # 3% errors = burn rate 30: pages before the outage ends
        self.assertLessEqual(self.first_page_minute(0.03, minutes=40), 30)

    def test_detection_speed_scales_with_severity(self):
        self.assertLessEqual(self.first_page_minute(0.50), 3)        # total outage: ~2 min
        self.assertLessEqual(self.first_page_minute(0.10), 10)       # 10% errors: ~8 min

    def test_alert_clears_within_short_window_after_fix(self):
        traffic = healthy(120) + [(RPM, 60)] * 40 + healthy(31)
        result = self.replay(traffic)
        self.assertIn("page", result[159])
        self.assertNotIn("page", result[-1])

    def test_budget_leak_raises_ticket_not_page(self):
        # 0.15% errors for 3 days: burn 1.5, spends the budget early but slowly
        a = BurnRateAlerter()
        last = []
        for _ in range(3 * 24 * 60):
            last = a.observe_minute(RPM, 3)
        self.assertEqual(last, ["ticket"])

    def test_blip_right_after_restart_does_not_page(self):
        # fresh alerter (no history) + 2% errors for 5 minutes: 1.67x burn over a real hour
        result = self.replay([(RPM, 40)] * 5)
        self.assertNotIn("page", result[-1])

    def test_total_outage_right_after_restart_still_pages_fast(self):
        result = self.replay([(RPM, RPM // 2)] * 5)
        first_page = next(i for i, m in enumerate(result) if "page" in m) + 1
        self.assertLessEqual(first_page, 5)

    def test_no_traffic_is_not_an_error(self):
        a = BurnRateAlerter()
        self.assertEqual([a.observe_minute(0, 0) for _ in range(100)][-1], [])

    def test_rejects_bad_counters(self):
        with self.assertRaises(ValueError):
            BurnRateAlerter().observe_minute(10, 11)


if __name__ == "__main__":
    unittest.main()

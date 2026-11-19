import unittest

from solution import (
    NULL_RECEIVER,
    UNROUTED_RECEIVER,
    Route,
    find_unrouted,
    naive_route,
    route_alert,
)

ROUTES = [
    Route({"severity": "critical"}, "pagerduty-oncall"),
    Route({"severity": "warning", "team": "payments"}, "slack-payments"),
    Route({"severity": "warning"}, "slack-alerts"),
    Route({"severity": "info"}, NULL_RECEIVER),
]


def alert(severity, team="checkout", name="CheckoutErrorRateHigh"):
    return {"alertname": name, "severity": severity, "team": team}


class ReproduceProblem(unittest.TestCase):
    def test_capitalised_critical_alert_is_discarded(self):
        self.assertEqual(naive_route(alert("Critical"), ROUTES), NULL_RECEIVER)

    def test_alert_without_severity_is_discarded(self):
        self.assertEqual(naive_route({"alertname": "DiskFull"}, ROUTES), NULL_RECEIVER)

    def test_lowercase_critical_alert_still_pages(self):
        self.assertEqual(naive_route(alert("critical"), ROUTES), "pagerduty-oncall")


class FixNormalisesRoutingLabels(unittest.TestCase):
    def test_capitalised_critical_alert_pages_on_call(self):
        self.assertEqual(route_alert(alert("Critical"), ROUTES).receiver, "pagerduty-oncall")

    def test_surrounding_spaces_are_ignored(self):
        self.assertEqual(route_alert(alert(" CRITICAL "), ROUTES).receiver, "pagerduty-oncall")

    def test_team_label_is_matched_case_insensitively(self):
        decision = route_alert(alert("warning", team="Payments"), ROUTES)
        self.assertEqual(decision.receiver, "slack-payments")

    def test_info_is_still_dropped_by_its_explicit_route(self):
        self.assertEqual(route_alert(alert("info"), ROUTES).receiver, NULL_RECEIVER)


class FixNeverDropsSilently(unittest.TestCase):
    def test_unknown_severity_goes_to_unrouted_receiver(self):
        decision = route_alert(alert("sev1"), ROUTES)
        self.assertEqual(decision.receiver, UNROUTED_RECEIVER)
        self.assertIn("sev1", decision.reason)

    def test_missing_severity_goes_to_unrouted_receiver(self):
        self.assertEqual(route_alert({"alertname": "DiskFull"}, ROUTES).receiver, UNROUTED_RECEIVER)

    def test_alert_matching_no_route_goes_to_unrouted_receiver(self):
        routes_without_warning = [route for route in ROUTES if route.matchers.get("severity") != "warning"]
        decision = route_alert(alert("warning"), routes_without_warning)
        self.assertEqual(decision.receiver, UNROUTED_RECEIVER)
        self.assertEqual(decision.reason, "no route matched")


class FixCiCheck(unittest.TestCase):
    def test_ci_check_lists_only_the_badly_labelled_rules(self):
        samples = [
            alert("critical", name="CheckoutDown"),
            alert("sev1", name="LegacyQueueStuck"),
            {"alertname": "DiskFull"},
        ]
        self.assertEqual(
            find_unrouted(samples, ROUTES),
            ["LegacyQueueStuck: invalid severity 'sev1'", "DiskFull: invalid severity None"],
        )

    def test_ci_check_passes_for_well_labelled_rules(self):
        self.assertEqual(find_unrouted([alert("Warning"), alert("info")], ROUTES), [])


if __name__ == "__main__":
    unittest.main()

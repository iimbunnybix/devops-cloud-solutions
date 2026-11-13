import unittest

from solution import CheckSpec, HttpResponse, availability, check, naive_check

CHECKOUT_URL = "https://shop.example.test/checkout/healthz"
MAINTENANCE_URL = "https://shop.example.test/maintenance"
HEALTHY_BODY = '{"status":"ok","db":"ok"}'
CHECKS_DURING_OUTAGE = 40  # one check per minute


class FakeSite:
    """Maps URLs to canned responses; flip `database_down` to simulate the outage."""

    def __init__(self, database_down: bool) -> None:
        self.database_down = database_down

    def get(self, url: str) -> HttpResponse:
        if url == MAINTENANCE_URL:
            return HttpResponse(200, body="<h1>We'll be right back</h1>")
        if url == CHECKOUT_URL and self.database_down:
            return HttpResponse(302, headers={"Location": MAINTENANCE_URL})
        if url == CHECKOUT_URL:
            return HttpResponse(200, body=HEALTHY_BODY)
        return HttpResponse(404)


def checkout_spec() -> CheckSpec:
    return CheckSpec(url=CHECKOUT_URL, body_must_contain='"status":"ok"')


class ReproduceProblem(unittest.TestCase):
    def test_naive_monitor_reports_up_during_outage(self):
        result = naive_check(FakeSite(database_down=True), CHECKOUT_URL)
        self.assertTrue(result.healthy)
        self.assertEqual(result.final_url, MAINTENANCE_URL)

    def test_dashboard_shows_full_availability_for_the_whole_outage(self):
        site = FakeSite(database_down=True)
        results = [naive_check(site, CHECKOUT_URL) for _ in range(CHECKS_DURING_OUTAGE)]
        self.assertEqual(availability(results), 1.0)


class FixStrictCheck(unittest.TestCase):
    def test_redirect_is_reported_as_down_with_its_target(self):
        result = check(FakeSite(database_down=True), checkout_spec())
        self.assertFalse(result.healthy)
        self.assertEqual(result.reason, f"status 302 to {MAINTENANCE_URL}")

    def test_outage_shows_zero_availability(self):
        site = FakeSite(database_down=True)
        results = [check(site, checkout_spec()) for _ in range(CHECKS_DURING_OUTAGE)]
        self.assertEqual(availability(results), 0.0)

    def test_healthy_site_passes(self):
        result = check(FakeSite(database_down=False), checkout_spec())
        self.assertTrue(result.healthy)

    def test_200_with_wrong_content_is_down(self):
        spec = CheckSpec(url=MAINTENANCE_URL, body_must_contain='"status":"ok"')
        result = check(FakeSite(database_down=False), spec)
        self.assertFalse(result.healthy)
        self.assertEqual(result.reason, "expected content missing")

    def test_expected_redirect_can_be_declared(self):
        spec = CheckSpec(url=CHECKOUT_URL, expected_status=302)
        self.assertTrue(check(FakeSite(database_down=True), spec).healthy)

    def test_no_results_is_not_reported_as_available(self):
        self.assertEqual(availability([]), 0.0)


if __name__ == "__main__":
    unittest.main()

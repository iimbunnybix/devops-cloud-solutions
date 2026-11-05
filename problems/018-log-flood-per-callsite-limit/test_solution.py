import unittest

from solution import (LINES_PER_WINDOW, PIPELINE_LINES_PER_SECOND, CallSiteRateLimiter,
                      LogRecord, interleave, naive_ship, pipeline_accepts)

FLOOD_LINES_PER_SECOND = 50_000
PAYMENT_ERRORS_PER_SECOND = 20


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def debug_flood(count: int) -> list[LogRecord]:
    return [LogRecord("orders", "orders/worker.py:88", f"DEBUG item={index}")
            for index in range(count)]


def payment_errors(count: int) -> list[LogRecord]:
    return [LogRecord("payments", "payments/charge.py:41", f"ERROR card declined id={index}")
            for index in range(count)]


def one_second_of_traffic() -> list[LogRecord]:
    return interleave(debug_flood(FLOOD_LINES_PER_SECOND), payment_errors(PAYMENT_ERRORS_PER_SECOND))


def count_service(lines: list[LogRecord], service: str) -> int:
    return sum(1 for line in lines if line.service == service)


def ship_through_limiter(limiter: CallSiteRateLimiter, records: list[LogRecord]) -> list[LogRecord]:
    shipped: list[LogRecord] = []
    for record in records:
        shipped.extend(limiter.submit(record))
    return shipped


class ReproduceProblem(unittest.TestCase):
    def test_flood_makes_pipeline_drop_most_payment_errors(self):
        offered = naive_ship(one_second_of_traffic())
        accepted = pipeline_accepts(offered, PIPELINE_LINES_PER_SECOND)
        self.assertEqual(len(offered), 50_020)
        self.assertEqual(count_service(accepted, "payments"), 2)     # 18 of 20 errors lost


class FixPerCallSiteLimit(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.limiter = CallSiteRateLimiter(self.clock)

    def test_every_payment_error_reaches_the_pipeline(self):
        shipped = ship_through_limiter(self.limiter, one_second_of_traffic())
        accepted = pipeline_accepts(shipped, PIPELINE_LINES_PER_SECOND)
        self.assertEqual(count_service(accepted, "payments"), PAYMENT_ERRORS_PER_SECOND)

    def test_flood_is_cut_to_the_per_site_limit(self):
        shipped = ship_through_limiter(self.limiter, debug_flood(FLOOD_LINES_PER_SECOND))
        self.assertEqual(len(shipped), LINES_PER_WINDOW)

    def test_suppressed_count_is_reported_when_the_next_window_starts(self):
        ship_through_limiter(self.limiter, debug_flood(100))
        self.clock.now = 1.0
        shipped = self.limiter.submit(debug_flood(1)[0])
        self.assertEqual(shipped[0].message, "suppressed 80 similar log lines")
        self.assertEqual(len(shipped), 2)                            # summary + the new line

    def test_flush_reports_suppression_for_a_site_that_went_quiet(self):
        ship_through_limiter(self.limiter, debug_flood(25))
        self.clock.now = 1.5
        summaries = self.limiter.flush()
        self.assertEqual([line.message for line in summaries], ["suppressed 5 similar log lines"])
        self.assertEqual(self.limiter.flush(), [])                   # reported only once

    def test_flush_before_window_ends_reports_nothing(self):
        ship_through_limiter(self.limiter, debug_flood(25))
        self.clock.now = 0.5
        self.assertEqual(self.limiter.flush(), [])

    def test_same_call_site_in_different_services_has_separate_budgets(self):
        same_site_other_service = LogRecord("billing", "orders/worker.py:88", "DEBUG")
        ship_through_limiter(self.limiter, debug_flood(LINES_PER_WINDOW))
        self.assertEqual(self.limiter.submit(same_site_other_service), [same_site_other_service])

    def test_quiet_site_below_limit_is_untouched_and_gets_no_summary(self):
        records = payment_errors(5)
        self.assertEqual(ship_through_limiter(self.limiter, records), records)
        self.clock.now = 2.0
        self.assertEqual(self.limiter.flush(), [])


class PipelineModel(unittest.TestCase):
    def test_pipeline_keeps_everything_under_capacity(self):
        lines = payment_errors(10)
        self.assertEqual(pipeline_accepts(lines, 10), lines)

    def test_pipeline_drops_everything_after_its_buffer_is_full(self):
        lines = debug_flood(1000)
        self.assertEqual(pipeline_accepts(lines, 100), lines[:100])


if __name__ == "__main__":
    unittest.main()

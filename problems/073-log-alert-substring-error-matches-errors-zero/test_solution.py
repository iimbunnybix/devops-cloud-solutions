import unittest

from solution import (alert_is_blind, healthy_minute, incident_minute, naive_error_count,
                      naive_should_page, parse_logfmt, should_page, summarize)


class ReproduceProblem(unittest.TestCase):
    def test_healthy_minute_counts_160_error_lines(self):
        self.assertEqual(naive_error_count(healthy_minute()), 160)

    def test_healthy_minute_pages_on_call(self):
        self.assertTrue(naive_should_page(healthy_minute()))

    def test_real_incident_is_hidden_in_the_noise(self):
        # 25 real errors only move the count from 160 to 185.
        self.assertEqual(naive_error_count(incident_minute()) - naive_error_count(healthy_minute()), 25)


class FixParseLogfmt(unittest.TestCase):
    def test_quoted_values_keep_their_spaces(self):
        fields = parse_logfmt('level=ERROR msg="payment declined by provider" attempt=3')
        self.assertEqual(fields, {"level": "ERROR", "msg": "payment declined by provider", "attempt": "3"})

    def test_word_error_inside_a_message_is_not_a_level(self):
        self.assertEqual(parse_logfmt('level=INFO msg="level=ERROR in docs"')["level"], "INFO")

    def test_line_without_fields_parses_to_nothing(self):
        self.assertEqual(parse_logfmt("Traceback (most recent call last):"), {})


class FixCountByLevel(unittest.TestCase):
    def test_healthy_minute_has_no_error_lines(self):
        self.assertEqual(summarize(healthy_minute()).error_lines, 0)

    def test_healthy_minute_does_not_page(self):
        self.assertFalse(should_page(summarize(healthy_minute())))

    def test_real_incident_pages(self):
        self.assertTrue(should_page(summarize(incident_minute())))

    def test_a_few_errors_stay_below_the_threshold(self):
        self.assertFalse(should_page(summarize(incident_minute(real_errors=9))))

    def test_lowercase_and_fatal_levels_count(self):
        summary = summarize(["level=error msg=x"] * 5 + ["level=FATAL msg=y"] * 5)
        self.assertEqual(summary.error_lines, 10)

    def test_warnings_do_not_count(self):
        self.assertEqual(summarize(["level=WARN msg=x"] * 20).error_lines, 0)


class FixBlindAlertWarning(unittest.TestCase):
    def test_normal_logs_are_not_blind(self):
        self.assertFalse(alert_is_blind(summarize(healthy_minute())))

    def test_switch_to_json_logs_makes_the_alert_blind(self):
        json_lines = ['{"level": "ERROR", "msg": "payment declined"}'] * 30
        summary = summarize(json_lines)
        self.assertFalse(should_page(summary))
        self.assertTrue(alert_is_blind(summary))

    def test_no_logs_at_all_is_blind(self):
        self.assertTrue(alert_is_blind(summarize([])))


if __name__ == "__main__":
    unittest.main()

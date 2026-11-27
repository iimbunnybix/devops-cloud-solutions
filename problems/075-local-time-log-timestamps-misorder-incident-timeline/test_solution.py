import unittest
from datetime import datetime, timezone

from solution import (FLAG_AMBIGUOUS, FLAG_NONEXISTENT, merge_timeline, naive_merge_timeline,
                      to_rfc3339)

# The 2026-03-14 incident. Real order (UTC): the Mumbai database primary dies
# at 13:30:00, New York app errors start 4 s later, London's load balancer
# sees 5xx, the replica is promoted, and the app recovers.
INCIDENT_LOGS = {
    "db-mumbai-1": [
        "2026-03-14 19:00:00 WARN postgres: primary lost heartbeat, starting failover",
        "2026-03-14 19:00:41 INFO postgres: replica promoted, accepting writes",
    ],
    "app-nyc-1": [
        "2026-03-14 09:30:04 ERROR orders-api: connection refused to db-primary",
        "2026-03-14 09:30:45 INFO orders-api: db connection restored",
    ],
    "lb-london-1": [
        "2026-03-14 13:30:20 WARN edge-lb: 5xx rate 38% on /orders",
    ],
}
ZONES = {
    "db-mumbai-1": "Asia/Kolkata",
    "app-nyc-1": "America/New_York",
    "lb-london-1": "Europe/London",
}


def messages(entries):
    return [entry.message.split(": ", 1)[1] for entry in entries]


def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)


class ReproduceProblem(unittest.TestCase):
    def test_naive_merge_puts_app_errors_before_the_failover_that_caused_them(self):
        order = messages(naive_merge_timeline(INCIDENT_LOGS))
        self.assertEqual(order, [
            "connection refused to db-primary",
            "db connection restored",
            "5xx rate 38% on /orders",
            "primary lost heartbeat, starting failover",
            "replica promoted, accepting writes",
        ])

    def test_naive_merge_shows_failover_nine_and_a_half_hours_after_app_errors(self):
        entries = naive_merge_timeline(INCIDENT_LOGS)
        first_error = datetime.fromisoformat(entries[0].local_text)
        failover = datetime.fromisoformat(entries[3].local_text)
        self.assertEqual((failover - first_error).total_seconds(), 9.5 * 3600 - 4)


class FixMergeByUtc(unittest.TestCase):
    def test_failover_comes_first_in_the_fixed_timeline(self):
        order = messages(merge_timeline(INCIDENT_LOGS, ZONES))
        self.assertEqual(order, [
            "primary lost heartbeat, starting failover",
            "connection refused to db-primary",
            "5xx rate 38% on /orders",
            "replica promoted, accepting writes",
            "db connection restored",
        ])

    def test_each_local_time_converts_to_the_right_utc_instant(self):
        times = [entry.utc_time for entry in merge_timeline(INCIDENT_LOGS, ZONES)]
        self.assertEqual(times, [utc(2026, 3, 14, 13, 30, 0), utc(2026, 3, 14, 13, 30, 4),
                                 utc(2026, 3, 14, 13, 30, 20), utc(2026, 3, 14, 13, 30, 41),
                                 utc(2026, 3, 14, 13, 30, 45)])

    def test_whole_incident_spans_45_seconds_not_hours(self):
        entries = merge_timeline(INCIDENT_LOGS, ZONES)
        self.assertEqual((entries[-1].utc_time - entries[0].utc_time).total_seconds(), 45)

    def test_ordinary_times_are_not_flagged(self):
        flags = [entry.flag for entry in merge_timeline(INCIDENT_LOGS, ZONES)]
        self.assertEqual(flags, [None] * 5)

    def test_host_without_a_configured_zone_is_rejected(self):
        with self.assertRaises(ValueError):
            merge_timeline({"mystery-host": ["2026-03-14 10:00:00 INFO x: y"]}, {})

    def test_output_can_be_written_as_rfc3339_utc(self):
        first = merge_timeline(INCIDENT_LOGS, ZONES)[0]
        self.assertEqual(to_rfc3339(first.utc_time), "2026-03-14T13:30:00Z")


class FixDaylightSavingEdges(unittest.TestCase):
    # New York clocks went back from 02:00 EDT to 01:00 EST on 2026-11-01,
    # so 01:00-01:59 happened twice.
    def fall_back_log(self):
        return {"app-nyc-1": [
            "2026-11-01 01:50:00 INFO orders-api: before the clocks went back",
            "2026-11-01 01:10:00 INFO orders-api: after the clocks went back",
        ]}

    def test_repeated_hour_is_flagged_as_ambiguous(self):
        flags = [e.flag for e in merge_timeline(self.fall_back_log(), ZONES)]
        self.assertEqual(flags, [FLAG_AMBIGUOUS, FLAG_AMBIGUOUS])

    def test_wall_clock_going_backwards_is_read_as_the_second_occurrence(self):
        times = [e.utc_time for e in merge_timeline(self.fall_back_log(), ZONES)]
        self.assertEqual(times, [utc(2026, 11, 1, 5, 50), utc(2026, 11, 1, 6, 10)])

    def test_naive_merge_reverses_lines_from_the_repeated_hour(self):
        order = messages(naive_merge_timeline(self.fall_back_log()))
        self.assertEqual(order, ["after the clocks went back", "before the clocks went back"])

    def test_time_skipped_by_spring_forward_is_flagged_as_nonexistent(self):
        logs = {"app-nyc-1": ["2026-03-08 02:30:00 INFO orders-api: impossible time"]}
        self.assertEqual(merge_timeline(logs, ZONES)[0].flag, FLAG_NONEXISTENT)

    def test_hour_after_the_repeated_hour_is_back_to_normal(self):
        logs = {"app-nyc-1": ["2026-11-01 01:50:00 INFO a: x", "2026-11-01 01:10:00 INFO a: y",
                              "2026-11-01 02:05:00 INFO a: z"]}
        last = merge_timeline(logs, ZONES)[-1]
        self.assertEqual((last.utc_time, last.flag), (utc(2026, 11, 1, 7, 5), None))

    def test_india_has_no_daylight_saving_so_nothing_is_flagged(self):
        logs = {"db-mumbai-1": ["2026-11-01 01:30:00 INFO postgres: checkpoint"]}
        entry = merge_timeline(logs, ZONES)[0]
        self.assertEqual((entry.utc_time, entry.flag), (utc(2026, 10, 31, 20, 0), None))


if __name__ == "__main__":
    unittest.main()

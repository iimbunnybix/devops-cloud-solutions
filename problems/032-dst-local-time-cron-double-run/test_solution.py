import unittest
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from solution import (AMBIGUOUS, NONEXISTENT, NORMAL, DailyJobScheduler, classify_local_time,
                      fixed_runs, local_midnight_utc, naive_runs, run_instant_utc,
                      runs_per_local_date)

NEW_YORK = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
BILLING_TIME = time(1, 30)          # inside the repeated hour when clocks go back
REPORT_TIME = time(2, 30)           # inside the skipped hour when clocks go forward
SPRING_FORWARD = date(2026, 3, 8)   # US: 02:00 EST -> 03:00 EDT
FALL_BACK = date(2026, 11, 1)       # US: 02:00 EDT -> 01:00 EST
DAYS_IN_2026 = 365


def one_local_day(day: date, zone: ZoneInfo = NEW_YORK) -> tuple[datetime, datetime]:
    """UTC start and end of local calendar ``day``."""
    return local_midnight_utc(day, zone), local_midnight_utc(day + timedelta(days=1), zone)


def year_2026() -> tuple[datetime, datetime]:
    """UTC start and end of 2026 in New York."""
    return local_midnight_utc(date(2026, 1, 1), NEW_YORK), local_midnight_utc(date(2027, 1, 1), NEW_YORK)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class ReproduceProblem(unittest.TestCase):
    def test_billing_runs_twice_on_the_night_clocks_go_back(self):
        runs = naive_runs(*one_local_day(FALL_BACK), BILLING_TIME, NEW_YORK)
        self.assertEqual(runs, [utc(2026, 11, 1, 5, 30), utc(2026, 11, 1, 6, 30)])

    def test_report_is_skipped_on_the_night_clocks_go_forward(self):
        self.assertEqual(naive_runs(*one_local_day(SPRING_FORWARD), REPORT_TIME, NEW_YORK), [])

    def test_over_a_year_naive_counts_are_off_by_one_each_way(self):
        start, end = year_2026()
        self.assertEqual(len(naive_runs(start, end, BILLING_TIME, NEW_YORK)), DAYS_IN_2026 + 1)
        self.assertEqual(len(naive_runs(start, end, REPORT_TIME, NEW_YORK)), DAYS_IN_2026 - 1)


class FixOneRunPerLocalDay(unittest.TestCase):
    def test_every_local_date_of_the_year_runs_exactly_once(self):
        start, end = year_2026()
        for wall_time in (BILLING_TIME, REPORT_TIME):
            counts = runs_per_local_date(fixed_runs(start, end, wall_time, NEW_YORK), NEW_YORK)
            self.assertEqual(len(counts), DAYS_IN_2026)
            self.assertEqual(set(counts.values()), {1})

    def test_ambiguous_time_runs_at_its_first_occurrence(self):
        runs = fixed_runs(*one_local_day(FALL_BACK), BILLING_TIME, NEW_YORK)
        self.assertEqual(runs, [utc(2026, 11, 1, 5, 30)])           # 01:30 EDT

    def test_nonexistent_time_runs_just_after_the_gap(self):
        runs = fixed_runs(*one_local_day(SPRING_FORWARD), REPORT_TIME, NEW_YORK)
        self.assertEqual(runs, [utc(2026, 3, 8, 7, 30)])
        self.assertEqual(runs[0].astimezone(NEW_YORK).time(), time(3, 30))

    def test_ordinary_day_runs_at_the_requested_local_time(self):
        instant = run_instant_utc(date(2026, 6, 15), BILLING_TIME, NEW_YORK)
        self.assertEqual(instant.astimezone(NEW_YORK).time(), BILLING_TIME)

    def test_classifies_normal_ambiguous_and_nonexistent_times(self):
        self.assertEqual(classify_local_time(FALL_BACK, BILLING_TIME, NEW_YORK), AMBIGUOUS)
        self.assertEqual(classify_local_time(SPRING_FORWARD, REPORT_TIME, NEW_YORK), NONEXISTENT)
        self.assertEqual(classify_local_time(SPRING_FORWARD, BILLING_TIME, NEW_YORK), NORMAL)

    def test_coarse_ticks_still_run_once_per_day(self):
        runs = fixed_runs(*one_local_day(FALL_BACK), BILLING_TIME, NEW_YORK,
                          tick=timedelta(minutes=7))
        self.assertEqual(len(runs), 1)

    def test_a_second_tick_after_running_does_nothing(self):
        scheduler = DailyJobScheduler(BILLING_TIME, NEW_YORK)
        self.assertTrue(scheduler.tick(utc(2026, 11, 1, 5, 30)))
        self.assertFalse(scheduler.tick(utc(2026, 11, 1, 6, 30)))   # 01:30 EST, the repeat

    def test_other_zones_with_different_transition_times(self):
        # Berlin falls back at 03:00 CEST -> 02:00 CET, so 02:30 repeats there.
        fall_back_berlin = date(2026, 10, 25)
        naive = naive_runs(*one_local_day(fall_back_berlin, BERLIN), REPORT_TIME, BERLIN)
        fixed = fixed_runs(*one_local_day(fall_back_berlin, BERLIN), REPORT_TIME, BERLIN)
        self.assertEqual(len(naive), 2)
        self.assertEqual(fixed, [utc(2026, 10, 25, 0, 30)])         # 02:30 CEST


if __name__ == "__main__":
    unittest.main()

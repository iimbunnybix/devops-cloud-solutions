import unittest
from datetime import date, datetime, timedelta, timezone

from solution import Gap, Shift, find_gaps, naive_every_day_covered, validate_schedule

HANDOFF_HOUR = 9
WEEK_START = datetime(2026, 11, 9, HANDOFF_HOUR, tzinfo=timezone.utc)   # a Monday
WEEK_END = WEEK_START + timedelta(days=7)
ENGINEERS = ["ana", "bob", "chen", "dev", "eli", "fay", "gus"]
SATURDAY = WEEK_START + timedelta(days=5)


def base_rotation() -> list[Shift]:
    """One 24-hour shift per engineer, handing off at 09:00 UTC every day."""
    shifts = []
    for day_index, person in enumerate(ENGINEERS):
        start = WEEK_START + timedelta(days=day_index)
        shifts.append(Shift(person, start, start + timedelta(days=1)))
    return shifts


def rotation_after_weekend_swap() -> list[Shift]:
    """Fay's Saturday shift deleted and replaced by a 12-hour override for Bob."""
    shifts = [shift for shift in base_rotation() if shift.person != "fay"]
    shifts.append(Shift("bob", SATURDAY, SATURDAY + timedelta(hours=12)))
    return shifts


def week_days() -> list[date]:
    return [(WEEK_START + timedelta(days=offset)).date() for offset in range(7)]


class ReproduceProblem(unittest.TestCase):
    def test_every_day_check_passes_despite_a_twelve_hour_hole(self):
        self.assertTrue(naive_every_day_covered(rotation_after_weekend_swap(), week_days()))


class FixFindsGaps(unittest.TestCase):
    def test_full_rotation_has_no_gaps(self):
        self.assertEqual(find_gaps(base_rotation(), WEEK_START, WEEK_END), [])

    def test_weekend_swap_gap_is_saturday_night_to_sunday_morning(self):
        gaps = find_gaps(rotation_after_weekend_swap(), WEEK_START, WEEK_END)
        expected = Gap(SATURDAY + timedelta(hours=12), SATURDAY + timedelta(days=1))
        self.assertEqual(gaps, [expected])
        self.assertEqual(gaps[0].duration, timedelta(hours=12))

    def test_override_overlapping_base_rotation_leaves_no_gap(self):
        shifts = base_rotation()
        shifts.append(Shift("bob", SATURDAY + timedelta(hours=3), SATURDAY + timedelta(hours=8)))
        self.assertEqual(find_gaps(shifts, WEEK_START, WEEK_END), [])

    def test_override_nested_inside_a_long_shift_does_not_shrink_coverage(self):
        long_shift = Shift("ana", WEEK_START, WEEK_END)
        inside = Shift("bob", WEEK_START + timedelta(hours=1), WEEK_START + timedelta(hours=2))
        self.assertEqual(find_gaps([long_shift, inside], WEEK_START, WEEK_END), [])

    def test_uncovered_start_and_end_of_window_are_reported(self):
        middle = Shift("ana", WEEK_START + timedelta(days=1), WEEK_START + timedelta(days=2))
        gaps = find_gaps([middle], WEEK_START, WEEK_END)
        self.assertEqual([gap.duration for gap in gaps], [timedelta(days=1), timedelta(days=5)])

    def test_empty_schedule_is_one_gap_covering_the_window(self):
        self.assertEqual(find_gaps([], WEEK_START, WEEK_END), [Gap(WEEK_START, WEEK_END)])

    def test_seconds_long_gap_from_hand_entered_times_is_ignored(self):
        first = Shift("ana", WEEK_START, WEEK_START + timedelta(hours=12) - timedelta(seconds=30))
        second = Shift("bob", WEEK_START + timedelta(hours=12), WEEK_END)
        self.assertEqual(find_gaps([first, second], WEEK_START, WEEK_END), [])


class FixValidatesBeforePublishing(unittest.TestCase):
    def test_gap_is_described_in_utc_with_its_length(self):
        problems = validate_schedule(rotation_after_weekend_swap(), WEEK_START, WEEK_END)
        self.assertEqual(problems, ["nobody on call 2026-11-14 21:00 to 2026-11-15 09:00 UTC (12.0h)"])

    def test_shift_ending_before_it_starts_is_reported_and_covers_nothing(self):
        shifts = [shift for shift in base_rotation() if shift.person != "fay"]
        shifts.append(Shift("bob", SATURDAY + timedelta(days=1), SATURDAY))   # start/end swapped
        problems = validate_schedule(shifts, WEEK_START, WEEK_END)
        self.assertEqual(len(problems), 2)
        self.assertIn("shift for bob ends at 2026-11-14 09:00", problems[0])
        self.assertIn("(24.0h)", problems[1])

    def test_valid_schedule_has_no_problems(self):
        self.assertEqual(validate_schedule(base_rotation(), WEEK_START, WEEK_END), [])


if __name__ == "__main__":
    unittest.main()

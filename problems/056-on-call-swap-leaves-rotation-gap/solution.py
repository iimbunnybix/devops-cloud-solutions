"""Check that an on-call schedule covers every minute before it goes live.

A weekend swap was entered as a 12-hour override and the original 24-hour
shift was deleted, leaving nobody on call from Saturday 21:00 to Sunday 09:00.
The schedule check only asked "does every day have someone on call?", which
was still true. This module merges all shifts into continuous covered
intervals and reports every uncovered gap in the window, plus shifts that
cannot be right (ending before they start), so the gap is found before a page.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# A shift may end at 09:00:00 and the next start at 09:00:00 - that is covered.
# Gaps shorter than this are rounding noise from hand-entered times, not holes.
DEFAULT_MIN_GAP = timedelta(minutes=1)


@dataclass(frozen=True)
class Shift:
    """One person on call from start (inclusive) to end (exclusive), in UTC."""
    person: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Gap:
    """A period inside the checked window where nobody is on call."""
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def naive_every_day_covered(shifts: list[Shift], days: list[date]) -> bool:
    """The original check: each calendar day has at least one shift touching it."""
    for day in days:
        day_has_shift = False
        for shift in shifts:
            if shift.start.date() <= day <= shift.end.date():
                day_has_shift = True
        if not day_has_shift:
            return False
    return True


def invalid_shifts(shifts: list[Shift]) -> list[Shift]:
    """Shifts whose end is not after their start; they cover nothing."""
    bad: list[Shift] = []
    for shift in shifts:
        if shift.end <= shift.start:
            bad.append(shift)
    return bad


def merged_coverage(shifts: list[Shift]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or touching shifts into non-overlapping covered intervals.

    Overlaps are normal (an override on top of the base rotation), so we only
    care about the union of all shifts, not who is on call.
    """
    valid = [shift for shift in shifts if shift.end > shift.start]
    valid.sort(key=lambda shift: shift.start)
    merged: list[tuple[datetime, datetime]] = []
    for shift in valid:
        if merged and shift.start <= merged[-1][1]:
            # Extends (or sits inside) the previous interval.
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, shift.end))
        else:
            merged.append((shift.start, shift.end))
    return merged


def find_gaps(shifts: list[Shift], window_start: datetime, window_end: datetime,
              min_gap: timedelta = DEFAULT_MIN_GAP) -> list[Gap]:
    """Every uncovered period of at least min_gap inside [window_start, window_end)."""
    gaps: list[Gap] = []
    cursor = window_start
    for covered_start, covered_end in merged_coverage(shifts):
        if covered_end <= cursor:
            continue
        if covered_start >= window_end:
            break
        if covered_start - cursor >= min_gap:
            gaps.append(Gap(cursor, covered_start))
        cursor = max(cursor, covered_end)
    # Whatever is left after the last shift is uncovered too.
    if window_end - cursor >= min_gap:
        gaps.append(Gap(cursor, window_end))
    return gaps


def validate_schedule(shifts: list[Shift], window_start: datetime,
                      window_end: datetime) -> list[str]:
    """Human-readable problems; an empty list means the schedule can be published."""
    problems: list[str] = []
    for shift in invalid_shifts(shifts):
        problems.append(f"shift for {shift.person} ends at {shift.end:%Y-%m-%d %H:%M} "
                        f"before it starts at {shift.start:%Y-%m-%d %H:%M}")
    for gap in find_gaps(shifts, window_start, window_end):
        hours = gap.duration.total_seconds() / 3600
        problems.append(f"nobody on call {gap.start:%Y-%m-%d %H:%M} to "
                        f"{gap.end:%Y-%m-%d %H:%M} UTC ({hours:.1f}h)")
    return problems

"""Catch cron entries that restrict both day-of-month and day-of-week.

An engineer wrote "0 2 1 * 1" meaning "02:00 on the 1st, if it is a Monday".
Standard (Vixie) cron does not AND those two fields: when both are restricted
it runs when EITHER matches, so the job ran on the 1st of every month AND on
every Monday. This module implements cron matching with the real OR rule,
keeps the engineer's AND assumption for comparison, lints entries that
restrict both fields, and rewrites them as a weekday-only schedule plus a
day-of-month guard that the job checks itself, which gives the intended runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

MINUTE_RANGE = (0, 59)
HOUR_RANGE = (0, 23)
DAY_OF_MONTH_RANGE = (1, 31)
MONTH_RANGE = (1, 12)
# Cron weekdays: 0 and 7 are both Sunday, 1 is Monday.
DAY_OF_WEEK_RANGE = (0, 7)
CRON_SUNDAY_ALIAS = 7
FIELD_COUNT = 5


@dataclass(frozen=True)
class CronEntry:
    """A parsed five-field cron schedule."""
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    # Vixie cron treats a field as "unrestricted" when its text starts with
    # "*" (so "*/2" counts as unrestricted too); the OR rule depends on this.
    day_of_month_restricted: bool
    day_of_week_restricted: bool


def parse_part(part: str, low: int, high: int) -> set[int]:
    """Parse one comma-separated piece: '*', 'N', 'A-B', with optional '/STEP'."""
    step = 1
    if "/" in part:
        part, step_text = part.split("/")
        step = int(step_text)
    if part == "*":
        start, end = low, high
    elif "-" in part:
        start_text, end_text = part.split("-")
        start, end = int(start_text), int(end_text)
    else:
        start = end = int(part)
    if start < low or end > high or start > end or step < 1:
        raise ValueError(f"value {part!r} out of range {low}-{high}")
    return set(range(start, end + 1, step))


def parse_field(text: str, value_range: tuple[int, int]) -> frozenset[int]:
    """Parse a whole field such as '1,15' or '*/10' into the allowed values."""
    values: set[int] = set()
    for part in text.split(","):
        values |= parse_part(part, value_range[0], value_range[1])
    return frozenset(values)


def parse_cron(expression: str) -> CronEntry:
    """Parse 'minute hour day-of-month month day-of-week'."""
    fields = expression.split()
    if len(fields) != FIELD_COUNT:
        raise ValueError(f"expected {FIELD_COUNT} fields, got {len(fields)}")
    days_of_week = set(parse_field(fields[4], DAY_OF_WEEK_RANGE))
    if CRON_SUNDAY_ALIAS in days_of_week:
        days_of_week.discard(CRON_SUNDAY_ALIAS)
        days_of_week.add(0)
    return CronEntry(
        parse_field(fields[0], MINUTE_RANGE), parse_field(fields[1], HOUR_RANGE),
        parse_field(fields[2], DAY_OF_MONTH_RANGE), parse_field(fields[3], MONTH_RANGE),
        frozenset(days_of_week),
        day_of_month_restricted=not fields[2].startswith("*"),
        day_of_week_restricted=not fields[4].startswith("*"))


def cron_weekday(day: date) -> int:
    """Convert Python's Monday=0 weekday to cron's Sunday=0 numbering."""
    return (day.weekday() + 1) % 7


def naive_assumed_day_matches(entry: CronEntry, day: date) -> bool:
    """What the author assumed: day-of-month AND day-of-week must both match."""
    return (day.month in entry.months and day.day in entry.days_of_month
            and cron_weekday(day) in entry.days_of_week)


def cron_day_matches(entry: CronEntry, day: date) -> bool:
    """What cron really does with the two day fields (Vixie/POSIX rule)."""
    if day.month not in entry.months:
        return False
    month_day_ok = day.day in entry.days_of_month
    weekday_ok = cron_weekday(day) in entry.days_of_week
    # Only when BOTH day fields are restricted does cron switch to OR.
    if entry.day_of_month_restricted and entry.day_of_week_restricted:
        return month_day_ok or weekday_ok
    return month_day_ok and weekday_ok


def run_times(expression: str, first_day: date, last_day: date,
              use_naive_assumption: bool = False) -> list[datetime]:
    """Every minute the entry fires between first_day and last_day inclusive."""
    entry = parse_cron(expression)
    day_matches = naive_assumed_day_matches if use_naive_assumption else cron_day_matches
    runs: list[datetime] = []
    day = first_day
    while day <= last_day:
        if day_matches(entry, day):
            for hour in sorted(entry.hours):
                for minute in sorted(entry.minutes):
                    runs.append(datetime(day.year, day.month, day.day, hour, minute))
        day += timedelta(days=1)
    return runs


def lint_cron(expression: str) -> list[str]:
    """Warn about entries whose day fields will be OR-ed, not AND-ed."""
    entry = parse_cron(expression)
    if entry.day_of_month_restricted and entry.day_of_week_restricted:
        return ["day-of-month and day-of-week are both set: cron runs when EITHER "
                "matches; use rewrite_with_day_guard for 'both must match'"]
    return []


def rewrite_with_day_guard(expression: str) -> tuple[str, frozenset[int]]:
    """Split an AND-intended entry into a weekday-only entry plus a day guard.

    The job (or a wrapper) checks the guard itself, e.g. in a shell wrapper:
    [ "$(date +%-d)" -eq 1 ] || exit 0
    """
    minute, hour, day_of_month, month, day_of_week = expression.split()
    guard_days = parse_field(day_of_month, DAY_OF_MONTH_RANGE)
    return f"{minute} {hour} * {month} {day_of_week}", guard_days


def guarded_run_times(expression: str, guard_days: frozenset[int],
                      first_day: date, last_day: date) -> list[datetime]:
    """Runs that actually do work: cron fires and the day guard passes."""
    runs: list[datetime] = []
    for run in run_times(expression, first_day, last_day):
        if run.day in guard_days:
            runs.append(run)
    return runs

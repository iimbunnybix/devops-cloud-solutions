"""Run a daily job exactly once per local day across daylight saving changes.

The original bug: the scheduler woke up every minute, converted the current
UTC time to New York local time and ran each job whose ``HH:MM`` matched.
On the night clocks go back, 01:00-01:59 happens twice, so the 01:30
billing run charged customers twice. On the night clocks go forward,
02:00-02:59 never happens, so the 02:30 report silently did not run.

Fix: for each local calendar date, resolve the job's wall-clock time to
exactly one UTC instant (the first occurrence of an ambiguous time, or the
moment just after the gap for a nonexistent one), and run when the clock
passes that instant if this date has not run yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NORMAL, AMBIGUOUS, NONEXISTENT = "normal", "ambiguous", "nonexistent"
TICK = timedelta(minutes=1)        # how often the scheduler process wakes up


def classify_local_time(day: date, wall_time: time, zone: ZoneInfo) -> str:
    """Say whether ``wall_time`` on ``day`` happens once, twice, or never in ``zone``."""
    first = datetime.combine(day, wall_time, tzinfo=zone)
    second = first.replace(fold=1)
    if first.utcoffset() != second.utcoffset():
        # WHY: zoneinfo gives the two folds different offsets both inside an
        # overlap and inside a gap; a round trip through UTC tells them apart.
        round_trip = first.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) == first.replace(tzinfo=None):
            return AMBIGUOUS
        return NONEXISTENT
    return NORMAL


def run_instant_utc(day: date, wall_time: time, zone: ZoneInfo) -> datetime:
    """The single UTC instant the job should run for local date ``day``.

    Ambiguous (clocks went back): the first occurrence, so the job runs at
    its usual offset from the previous day. Nonexistent (clocks went
    forward): fold=0 maps it past the gap, e.g. 02:30 becomes 03:30 local,
    which is the earliest real moment after the requested time.
    """
    local = datetime.combine(day, wall_time, tzinfo=zone)   # fold=0 by default
    return local.astimezone(timezone.utc)


def naive_runs(start_utc: datetime, end_utc: datetime, wall_time: time,
               zone: ZoneInfo) -> list[datetime]:
    """The original scheduler: run whenever the local clock reads ``HH:MM``."""
    runs = []
    now = start_utc
    while now < end_utc:
        local = now.astimezone(zone)
        if (local.hour, local.minute) == (wall_time.hour, wall_time.minute):
            runs.append(now)
        now += TICK
    return runs


@dataclass
class DailyJobScheduler:
    """Fires a job once per local calendar date at a resolved UTC instant."""

    wall_time: time
    zone: ZoneInfo
    last_run_date: date | None = None
    runs: list[datetime] = field(default_factory=list)

    def tick(self, now_utc: datetime) -> bool:
        """Run the job if today's instant has passed and today has not run yet."""
        today = now_utc.astimezone(self.zone).date()
        if self.last_run_date == today:
            return False
        if now_utc < run_instant_utc(today, self.wall_time, self.zone):
            return False
        # WHY: keyed by local date, not by wall-clock match, so a repeated
        # hour cannot trigger a second run and a skipped hour cannot hide one.
        self.last_run_date = today
        self.runs.append(now_utc)
        return True


def fixed_runs(start_utc: datetime, end_utc: datetime, wall_time: time, zone: ZoneInfo,
               tick: timedelta = TICK) -> list[datetime]:
    """Drive ``DailyJobScheduler`` with a clock that advances by ``tick``."""
    scheduler = DailyJobScheduler(wall_time, zone)
    now = start_utc
    while now < end_utc:
        scheduler.tick(now)
        now += tick
    return scheduler.runs


def runs_per_local_date(runs: list[datetime], zone: ZoneInfo) -> dict[date, int]:
    """Count runs by the local date they happened on."""
    counts: dict[date, int] = {}
    for run in runs:
        local_day = run.astimezone(zone).date()
        counts[local_day] = counts.get(local_day, 0) + 1
    return counts


def local_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    """Midnight at the start of local ``day``, as a UTC instant."""
    return datetime.combine(day, time(0, 0), tzinfo=zone).astimezone(timezone.utc)

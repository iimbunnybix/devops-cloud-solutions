"""Track the error budget over a rolling window, not a calendar month.

The team's deploy policy froze risky deploys when the month's error budget
ran out. The budget was computed per calendar month, so it reset to 100% on
the 1st. A big incident on 31 January and another on 2 February each used
most of "their" month's budget, so both months looked fine and deploys never
froze, although users had just lived through 1.5x the budget in three days.

Fix: compute the budget over the last 30 days (a rolling window) every day,
so an incident keeps counting against the budget for 30 days whatever the
calendar says.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

SLO_TARGET = 0.999                    # 99.9% of requests succeed
ALLOWED_ERROR_RATIO = 1 - SLO_TARGET
ROLLING_WINDOW_DAYS = 30


@dataclass(frozen=True)
class DailyCounts:
    requests: int
    errors: int


def budget_used(days: list[DailyCounts]) -> float:
    """Share of the error budget used by ``days`` (1.0 = exactly all of it)."""
    total_requests = 0
    total_errors = 0
    for counts in days:
        total_requests += counts.requests
        total_errors += counts.errors
    if total_requests == 0:
        return 0.0
    allowed_errors = total_requests * ALLOWED_ERROR_RATIO
    return total_errors / allowed_errors


def calendar_month_budget_used(history: dict[date, DailyCounts], today: date,
                               expected_requests_per_day: int) -> float:
    """Original report: errors since the 1st against a fixed monthly allowance.

    The allowance is planned traffic for the whole month times the allowed
    error ratio, and it starts again from zero errors on the 1st.
    """
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    allowed_errors = expected_requests_per_day * days_in_month * ALLOWED_ERROR_RATIO
    errors_so_far = 0
    for day, counts in history.items():
        if day.year == today.year and day.month == today.month and day <= today:
            errors_so_far += counts.errors
    return errors_so_far / allowed_errors


def rolling_budget_used(history: dict[date, DailyCounts], today: date,
                        window_days: int = ROLLING_WINDOW_DAYS) -> float:
    """Budget used over the ``window_days`` days ending with ``today``."""
    first_day = today - timedelta(days=window_days - 1)
    window = []
    for day, counts in history.items():
        if first_day <= day <= today:
            window.append(counts)
    return budget_used(window)


def original_deploys_allowed(history: dict[date, DailyCounts], today: date,
                             expected_requests_per_day: int) -> bool:
    """Original policy: freeze only when this calendar month's budget is spent."""
    return calendar_month_budget_used(history, today, expected_requests_per_day) < 1.0


def deploys_allowed(history: dict[date, DailyCounts], today: date) -> bool:
    """Fixed policy: freeze while the rolling 30-day budget is spent."""
    return rolling_budget_used(history, today) < 1.0


def first_day_deploys_resume(history: dict[date, DailyCounts], frozen_on: date,
                             days_to_check: int = 60) -> date | None:
    """First day after ``frozen_on`` on which the rolling budget allows deploys again."""
    for offset in range(1, days_to_check + 1):
        day = frozen_on + timedelta(days=offset)
        if deploys_allowed(history, day):
            return day
    return None


def build_history(start: date, end: date, requests_per_day: int, baseline_errors: int,
                  incident_errors: dict[date, int]) -> dict[date, DailyCounts]:
    """Daily counts with a steady baseline plus extra errors on incident days."""
    history = {}
    day = start
    while day <= end:
        errors = baseline_errors + incident_errors.get(day, 0)
        history[day] = DailyCounts(requests_per_day, errors)
        day += timedelta(days=1)
    return history

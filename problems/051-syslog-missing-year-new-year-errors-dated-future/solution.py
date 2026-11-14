"""Give year-less syslog timestamps the right year, even across New Year.

Classic syslog lines (RFC 3164) carry "Dec 31 23:58:12" with no year. The log
shipper filled in the collector's current year, so just after midnight on
1 January every line from 31 December was dated almost a year in the future:
the "errors in the last 10 minutes" alert never counted them. Lines from
29 February even crashed the parser in a non-leap year. This module tries the
neighbouring years and picks the latest one that is not in the future.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

SYSLOG_TIME_FORMAT = "%Y %b %d %H:%M:%S"
# "Dec 31 23:58:12" is always 15 characters; "Jan  1" pads the day with a space.
SYSLOG_TIMESTAMP_LENGTH = 15
# A sending host's clock may run a little ahead of the collector's clock.
# Within this tolerance a timestamp is still accepted as "now", not next year.
FUTURE_TOLERANCE = timedelta(hours=1)
ERROR_MARKER = " ERROR "


def split_line(line: str) -> tuple[str, str]:
    """Split a syslog line into its timestamp text and the rest of the message."""
    return line[:SYSLOG_TIMESTAMP_LENGTH], line[SYSLOG_TIMESTAMP_LENGTH:]


def naive_parse_timestamp(timestamp_text: str, now: datetime) -> datetime:
    """Original parser: assume every line was written in the collector's current year."""
    return datetime.strptime(f"{now.year} {timestamp_text}", SYSLOG_TIME_FORMAT)


def parse_with_year(timestamp_text: str, year: int) -> datetime | None:
    """Parse the timestamp in a given year; None if that date does not exist (Feb 29)."""
    try:
        return datetime.strptime(f"{year} {timestamp_text}", SYSLOG_TIME_FORMAT)
    except ValueError:
        return None


def parse_timestamp(timestamp_text: str, now: datetime) -> datetime:
    """Pick the year that puts the timestamp closest to, but not after, `now`.

    Next year is a candidate too: on 31 Dec 23:59 a host whose clock is ahead
    may already log "Jan  1 00:00:05". Among candidates no later than
    now + FUTURE_TOLERANCE, the latest one is the most recent plausible moment.
    """
    latest_allowed = now + FUTURE_TOLERANCE
    best: datetime | None = None
    for year in (now.year + 1, now.year, now.year - 1):
        candidate = parse_with_year(timestamp_text, year)
        if candidate is None or candidate > latest_allowed:
            continue
        if best is None or candidate > best:
            best = candidate
    if best is None:
        # Only possible for Feb 29 when neither neighbouring year is a leap year.
        raise ValueError(f"no plausible year for syslog timestamp {timestamp_text!r}")
    return best


def is_error(message: str) -> bool:
    """True for lines the alert counts as errors."""
    return ERROR_MARKER in message


def count_recent_errors(lines: list[str], now: datetime, window: timedelta,
                        parse: Callable[[str, datetime], datetime] = parse_timestamp) -> int:
    """Count error lines whose timestamp falls in (now - window, now + tolerance].

    `parse` is either parse_timestamp or naive_parse_timestamp so the tests
    can run the same alert with the old and the new parser.
    """
    window_start = now - window
    window_end = now + FUTURE_TOLERANCE
    count = 0
    for line in lines:
        timestamp_text, message = split_line(line)
        if not is_error(message):
            continue
        logged_at = parse(timestamp_text, now)
        if window_start < logged_at <= window_end:
            count += 1
    return count

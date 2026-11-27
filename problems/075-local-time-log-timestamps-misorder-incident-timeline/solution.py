"""Incident timeline merged from local-time logs puts effects before causes.

Hosts in Mumbai, New York and London write log timestamps in their own local
time with no UTC offset ("2026-03-14 09:30:04"). The incident tool merged all
hosts by sorting those strings, so the Mumbai database failover appeared hours
*after* the New York app errors it caused. This module parses each host's
lines with that host's configured IANA time zone, converts them to aware UTC
datetimes, flags wall times that are ambiguous or impossible because of a
daylight-saving change, and only then sorts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Every log line starts with "YYYY-MM-DD HH:MM:SS" (19 characters).
LOCAL_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
TIMESTAMP_LENGTH = 19

FLAG_AMBIGUOUS = "ambiguous"      # wall time happened twice (clocks went back)
FLAG_NONEXISTENT = "nonexistent"  # wall time never happened (clocks went forward)


@dataclass(frozen=True)
class TimelineEntry:
    """One log line placed on the merged timeline."""

    utc_time: datetime | None  # None only in the naive version, which never converts
    host: str
    local_text: str            # the timestamp exactly as the host wrote it
    message: str
    flag: str | None = None


def split_line(line: str) -> tuple[str, str]:
    """Split a log line into its local timestamp text and its message."""
    timestamp_text = line[:TIMESTAMP_LENGTH]
    message = line[TIMESTAMP_LENGTH:].strip()
    return timestamp_text, message


# ---------------------------------------------------------------- original
def naive_merge_timeline(logs_by_host: dict[str, list[str]]) -> list[TimelineEntry]:
    """Original tool: sort every line by its timestamp string.

    The strings sort correctly only if every host uses the same clock. With
    local times from different zones, the order is meaningless.
    """
    entries = []
    for host, lines in logs_by_host.items():
        for line in lines:
            timestamp_text, message = split_line(line)
            entries.append(TimelineEntry(None, host, timestamp_text, message))
    entries.sort(key=lambda entry: entry.local_text)
    return entries


# ---------------------------------------------------------------- the fix
def is_ambiguous(local_time: datetime) -> bool:
    """True if fold=0 and fold=1 give different offsets.

    For a wall time that exists, that means it occurred twice (clocks went
    back). Call ``is_nonexistent`` first, because skipped times also differ.
    """
    first = local_time.replace(fold=0).utcoffset()
    second = local_time.replace(fold=1).utcoffset()
    return first != second


def is_nonexistent(local_time: datetime) -> bool:
    """True if the clocks skipped over this wall time.

    A real wall time survives a round trip through UTC unchanged; a skipped
    one comes back shifted by the size of the jump.
    """
    round_trip = local_time.astimezone(timezone.utc).astimezone(local_time.tzinfo)
    return round_trip.replace(tzinfo=None) != local_time.replace(tzinfo=None)


def parse_host_lines(host: str, lines: list[str], zone: ZoneInfo) -> list[TimelineEntry]:
    """Convert one host's lines, in file order, to UTC timeline entries.

    In the repeated hour after clocks go back, the same wall time means two
    different instants. A host writes its log in order, so if a timestamp in
    that hour goes *backwards* compared to the previous line, the clock has
    been set back and from then on we read the hour as its second occurrence
    (fold=1). The entry is still flagged, because a human should check it.
    """
    entries = []
    previous_local: datetime | None = None
    fold = 0
    for line in lines:
        timestamp_text, message = split_line(line)
        naive_local = datetime.strptime(timestamp_text, LOCAL_TIMESTAMP_FORMAT)
        local_time = naive_local.replace(tzinfo=zone)
        flag = None
        # Check skipped times first: zoneinfo also gives them two offsets,
        # so they would otherwise look ambiguous too.
        if is_nonexistent(local_time):
            # Keep the pre-jump offset (fold=0); the flag tells a human to check.
            flag = FLAG_NONEXISTENT
        elif is_ambiguous(local_time):
            flag = FLAG_AMBIGUOUS
            if previous_local is not None and naive_local < previous_local:
                fold = 1
            local_time = local_time.replace(fold=fold)
        else:
            # Outside the repeated hour, start fresh for the next transition.
            fold = 0
        utc_time = local_time.astimezone(timezone.utc)
        entries.append(TimelineEntry(utc_time, host, timestamp_text, message, flag))
        previous_local = naive_local
    return entries


def merge_timeline(logs_by_host: dict[str, list[str]],
                   zone_by_host: dict[str, str]) -> list[TimelineEntry]:
    """Merge all hosts' logs onto one timeline ordered by real (UTC) time.

    A host with no configured zone is an error, not a guess: guessing is
    exactly how the original tool produced a misleading timeline.
    """
    entries = []
    for host, lines in logs_by_host.items():
        if host not in zone_by_host:
            raise ValueError(f"no time zone configured for host {host!r}")
        zone = ZoneInfo(zone_by_host[host])
        entries.extend(parse_host_lines(host, lines, zone))
    # Python's sort is stable, so lines with the same instant keep file order.
    entries.sort(key=lambda entry: entry.utc_time)
    return entries


def to_rfc3339(utc_time: datetime) -> str:
    """Format a UTC time the way hosts should log it: unambiguous, sortable."""
    return utc_time.isoformat(timespec="seconds").replace("+00:00", "Z")


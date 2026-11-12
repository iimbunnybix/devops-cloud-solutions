"""Spread a fleet-wide hourly cron job across a window instead of minute 0.

Every host in a fleet of thousands ran the same metrics/package sync from the
same crontab line, "0 * * * *". All clocks are NTP-synced, so every host hit
the central server in the same second at :00 and most requests timed out.

The fix gives each host a fixed "splay": a stable hash of the job name and
hostname picks an offset inside a window (for example the first 10 minutes of
the hour). Each host always runs at the same time, which keeps logs and
debugging predictable, but the fleet as a whole is spread evenly.
"""
from __future__ import annotations

import hashlib
from collections import Counter

SECONDS_PER_MINUTE = 60
JOB_PERIOD_SECONDS = 3600            # "0 * * * *" runs once an hour
DEFAULT_SPLAY_WINDOW_SECONDS = 600   # spread starts over the first 10 minutes
HASH_BYTES_USED = 8                  # 64 bits of the digest is plenty for a modulo


def naive_start_offset(hostname: str, job_name: str) -> int:
    """Original crontab: every host starts at second 0 of the hour."""
    return 0


def splay_offset_seconds(hostname: str, job_name: str,
                         window_seconds: int = DEFAULT_SPLAY_WINDOW_SECONDS) -> int:
    """Seconds after the scheduled time at which this host should start the job.

    The same hostname and job always give the same offset, on every host,
    after every reboot and in every Python process.
    """
    validate_window(window_seconds)
    if not hostname:
        raise ValueError("hostname must not be empty")
    # Python's built-in hash() is randomly salted per process, so it would
    # give a different offset after every restart. SHA-256 is the same everywhere.
    key = f"{job_name}:{hostname}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    number = int.from_bytes(digest[:HASH_BYTES_USED], "big")
    return number % window_seconds


def validate_window(window_seconds: int) -> None:
    """Reject windows that would make a run overlap the next scheduled run."""
    if window_seconds < 1:
        raise ValueError("splay window must be at least 1 second")
    if window_seconds > JOB_PERIOD_SECONDS:
        # A 90-minute splay on an hourly job would push some runs into the next hour.
        raise ValueError("splay window must not be longer than the job period")


def splayed_cron_line(hostname: str, job_name: str, command: str,
                      window_seconds: int = DEFAULT_SPLAY_WINDOW_SECONDS) -> str:
    """Render the host's crontab line: minute from the offset, plus a short sleep.

    Cron only has minute resolution, so the seconds part is a ``sleep``.
    """
    offset = splay_offset_seconds(hostname, job_name, window_seconds)
    minute = offset // SECONDS_PER_MINUTE
    seconds = offset % SECONDS_PER_MINUTE
    if seconds == 0:
        return f"{minute} * * * * {command}"
    return f"{minute} * * * * sleep {seconds} && {command}"


def start_offsets(hostnames: list[str], job_name: str, use_splay: bool,
                  window_seconds: int = DEFAULT_SPLAY_WINDOW_SECONDS) -> list[int]:
    """Start second (relative to the hour) of the job on every host."""
    offsets = []
    for hostname in hostnames:
        if use_splay:
            offsets.append(splay_offset_seconds(hostname, job_name, window_seconds))
        else:
            offsets.append(naive_start_offset(hostname, job_name))
    return offsets


def requests_per_second(offsets: list[int]) -> Counter[int]:
    """How many hosts call the central server in each second of the hour."""
    return Counter(offsets)


def peak_requests_per_second(offsets: list[int]) -> int:
    """The busiest second the central server sees."""
    if not offsets:
        return 0
    return max(requests_per_second(offsets).values())


def seconds_over_capacity(offsets: list[int], capacity_per_second: int) -> int:
    """Number of seconds in which more hosts arrive than the server can handle."""
    overloaded = 0
    for count in requests_per_second(offsets).values():
        if count > capacity_per_second:
            overloaded += 1
    return overloaded


def fleet_hostnames(count: int, prefix: str = "node") -> list[str]:
    """Hostnames like node-00001 for building a fleet in tests and examples."""
    names = []
    for index in range(1, count + 1):
        names.append(f"{prefix}-{index:05d}")
    return names

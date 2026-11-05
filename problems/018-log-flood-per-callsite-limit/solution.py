"""Stop one noisy log line from drowning every other service's logs.

A debug statement left enabled inside a hot loop wrote tens of thousands of
identical lines per second. The shared log pipeline has a fixed ingest rate,
so it dropped lines at random: the flood crowded out other services' errors
and the logging bill spiked. This module limits each *call site* (where the
log line comes from) to a small number of lines per time window, counts what
it suppressed, and reports that count once the window ends, so engineers
still know the flood happened without paying for every copy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

LINES_PER_WINDOW = 20           # per call site; quiet code is never affected
WINDOW_SECONDS = 1.0
PIPELINE_LINES_PER_SECOND = 5_000


@dataclass(frozen=True)
class LogRecord:
    """One log line and where in the code it came from."""
    service: str
    call_site: str               # e.g. "orders/worker.py:88"
    message: str


@dataclass
class CallSiteWindow:
    """Lines emitted and suppressed for one call site in the current window."""
    window_start: float
    emitted: int = 0
    suppressed: int = 0


def summary_record(call_site_key: tuple[str, str], suppressed: int) -> LogRecord:
    """The single line that replaces ``suppressed`` dropped copies."""
    service, call_site = call_site_key
    return LogRecord(service, call_site, f"suppressed {suppressed} similar log lines")


class CallSiteRateLimiter:
    """Allows ``LINES_PER_WINDOW`` lines per call site per window; summarises the rest."""

    def __init__(self, clock: Callable[[], float], lines_per_window: int = LINES_PER_WINDOW,
                 window_seconds: float = WINDOW_SECONDS) -> None:
        self._clock = clock
        self._lines_per_window = lines_per_window
        self._window_seconds = window_seconds
        self._windows: dict[tuple[str, str], CallSiteWindow] = {}

    def submit(self, record: LogRecord) -> list[LogRecord]:
        """Return the lines to actually ship for this record (0, 1 or 2 lines)."""
        key = (record.service, record.call_site)
        now = self._clock()
        output = self._roll_window_if_expired(key, now)
        window = self._windows[key]
        if window.emitted < self._lines_per_window:
            window.emitted += 1
            output.append(record)
        else:
            window.suppressed += 1
        return output

    def flush(self) -> list[LogRecord]:
        """Emit summaries for windows that ended while their call site went quiet."""
        now = self._clock()
        output: list[LogRecord] = []
        for key in list(self._windows):
            output.extend(self._roll_window_if_expired(key, now))
        return output

    def _roll_window_if_expired(self, key: tuple[str, str], now: float) -> list[LogRecord]:
        """Start a fresh window for ``key`` if needed; return the old window's summary."""
        window = self._windows.get(key)
        if window is not None and now - window.window_start < self._window_seconds:
            return []
        self._windows[key] = CallSiteWindow(window_start=now)
        # Without this summary, rate limiting would silently hide how bad the flood was.
        if window is not None and window.suppressed > 0:
            return [summary_record(key, window.suppressed)]
        return []


def naive_ship(records: list[LogRecord]) -> list[LogRecord]:
    """Original behaviour: every log line goes to the pipeline."""
    return list(records)


def pipeline_accepts(lines_offered: list[LogRecord], capacity: int) -> list[LogRecord]:
    """Model the shared pipeline for one second: a fixed-size ingest buffer.

    Once ``capacity`` lines have arrived the buffer is full and every later
    line in that second is dropped, no matter which service sent it.
    """
    return list(lines_offered[:capacity])


def interleave(flood: list[LogRecord], quiet: list[LogRecord]) -> list[LogRecord]:
    """Spread the quiet service's lines evenly through the flood, like real traffic."""
    if not quiet:
        return list(flood)
    merged: list[LogRecord] = []
    every = max(1, len(flood) // len(quiet))
    quiet_index = 0
    for position, line in enumerate(flood):
        if position % every == 0 and quiet_index < len(quiet):
            merged.append(quiet[quiet_index])
            quiet_index += 1
        merged.append(line)
    merged.extend(quiet[quiet_index:])
    return merged

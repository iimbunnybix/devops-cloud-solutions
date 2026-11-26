"""A log alert matched the word "error" anywhere in a line and paged all night.

The alert counted log lines containing "error" (case-insensitive) per minute.
A deploy added ``errors=0`` to every worker's heartbeat, and the docs site
logs requests for ``/docs/error-codes``, so a healthy minute crossed the
threshold and paged on-call every minute. The fix parses each logfmt line
and counts only lines whose ``level`` field is ERROR or FATAL. Lines that
cannot be parsed are counted separately, so a format change cannot silently
switch the alert off.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PAGE_THRESHOLD_PER_MINUTE = 10
UNPARSED_WARN_SHARE = 0.05           # >5% unparsable lines means the alert is half-blind
ALERTING_LEVELS = {"ERROR", "FATAL", "CRITICAL"}
WORKERS = 40

# key=value or key="quoted value", the logfmt format our services emit.
LOGFMT_PAIR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)')


# ---------------------------------------------------------------- sample logs
def healthy_minute() -> list[str]:
    """A normal minute after the deploy: no real errors at all."""
    lines = []
    for worker in range(WORKERS):
        lines.append(f'level=INFO msg="heartbeat" worker={worker} errors=0 retries=0')
    for request in range(120):
        lines.append(f'level=INFO msg="GET /docs/error-codes 200" request_id=r{request}')
    lines.append('level=WARN msg="slow query" duration_ms=950')
    return lines


def incident_minute(real_errors: int = 25) -> list[str]:
    """A minute where payments really are failing."""
    lines = healthy_minute()
    for attempt in range(real_errors):
        lines.append(f'level=ERROR msg="payment declined by provider" attempt={attempt}')
    return lines


# ---------------------------------------------------------------- original
def naive_error_count(lines: list[str]) -> int:
    """Original rule: any line mentioning 'error' counts."""
    count = 0
    for line in lines:
        if "error" in line.lower():
            count += 1
    return count


def naive_should_page(lines: list[str]) -> bool:
    return naive_error_count(lines) >= PAGE_THRESHOLD_PER_MINUTE


# ---------------------------------------------------------------- the fix
def parse_logfmt(line: str) -> dict[str, str]:
    """Return the line's key=value fields; quotes around values are removed."""
    fields: dict[str, str] = {}
    for key, raw_value in LOGFMT_PAIR.findall(line):
        if raw_value.startswith('"') and raw_value.endswith('"') and len(raw_value) >= 2:
            raw_value = raw_value[1:-1]
        fields[key] = raw_value
    return fields


@dataclass(frozen=True)
class MinuteSummary:
    error_lines: int
    unparsed_lines: int
    total_lines: int


def summarize(lines: list[str]) -> MinuteSummary:
    """Count real error lines by their level field, and lines with no level."""
    errors = 0
    unparsed = 0
    for line in lines:
        level = parse_logfmt(line).get("level")
        if level is None:
            # Not our format: count it so a format change is visible.
            unparsed += 1
        elif level.upper() in ALERTING_LEVELS:
            errors += 1
    return MinuteSummary(errors, unparsed, len(lines))


def should_page(summary: MinuteSummary) -> bool:
    """Page on real error volume only."""
    return summary.error_lines >= PAGE_THRESHOLD_PER_MINUTE


def alert_is_blind(summary: MinuteSummary) -> bool:
    """Warn (not page) when too many lines have no level to judge.

    Without this, a logger that switches to JSON would drop the error count
    to zero and the alert would look healthy forever.
    """
    if summary.total_lines == 0:
        return True
    return summary.unparsed_lines / summary.total_lines > UNPARSED_WARN_SHARE

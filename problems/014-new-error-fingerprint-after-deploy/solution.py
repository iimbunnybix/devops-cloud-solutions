"""Detect new and surging error types after a deploy by fingerprinting logs.

Raw error messages almost never repeat exactly: they contain order ids,
UUIDs, durations, IP addresses and user input. Grouping by the raw message
gives one group per log line, and alerting on the total error rate hides a
new, small error behind the noise of known ones.

``fingerprint`` normalises a log event into a stable template (variable
parts masked) and hashes it together with the service, exception type and top
stack frame. ``compare_windows`` then compares a baseline window (before the
deploy) with the window after it, normalised by request volume, and reports:

* **new** fingerprints that never appeared in the baseline, and
* **surging** fingerprints whose rate per request rose by ``surge_ratio``.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

# Order matters: mask the most specific patterns first.
_MASKS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<uuid>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b"), "<email>"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<hex>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"\b\d+(\.\d+)?(ms|s|kb|mb|gb|b)?\b", re.I), "<num>"),
]


def template(message: str) -> str:
    """Replace variable parts of a log message with placeholders."""
    out = message.strip()
    for pattern, token in _MASKS:
        out = pattern.sub(token, out)
    return re.sub(r"\s+", " ", out)


@dataclass(frozen=True)
class ErrorEvent:
    service: str
    exc_type: str
    message: str
    top_frame: str = ""         # e.g. "orders/pricing.py:apply_coupon"


def fingerprint(e: ErrorEvent) -> str:
    """Stable 12-hex-char id for the *kind* of error, not the instance.

    Line numbers are stripped from the frame so a deploy that only moves code
    does not make every known error look new.
    """
    frame = re.sub(r":\d+$", "", e.top_frame)
    key = "\x1f".join([e.service, e.exc_type, template(e.message), frame])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


@dataclass
class Finding:
    fingerprint: str
    kind: str                   # "new" | "surging"
    count: int
    baseline_count: int
    rate_ratio: float | None    # post rate / baseline rate (None if new)
    example: str


def compare_windows(baseline: list[ErrorEvent], baseline_requests: int,
                    after: list[ErrorEvent], after_requests: int, *,
                    min_count: int = 5, surge_ratio: float = 3.0) -> list[Finding]:
    """Findings sorted by count, largest first.

    ``min_count`` keeps one-off errors from paging anyone; ``surge_ratio``
    compares rates per request, so a traffic increase alone is not a surge.
    """
    if baseline_requests <= 0 or after_requests <= 0:
        raise ValueError("request counts must be positive")
    base = Counter(fingerprint(e) for e in baseline)
    post = Counter()
    examples: dict[str, str] = {}
    for e in after:
        fp = fingerprint(e)
        post[fp] += 1
        examples.setdefault(fp, f"{e.service} {e.exc_type}: {template(e.message)}")
    findings = []
    for fp, n in post.items():
        if n < min_count:
            continue
        b = base.get(fp, 0)
        if b == 0:
            findings.append(Finding(fp, "new", n, 0, None, examples[fp]))
            continue
        ratio = (n / after_requests) / (b / baseline_requests)
        if ratio >= surge_ratio:
            findings.append(Finding(fp, "surging", n, b, round(ratio, 2), examples[fp]))
    findings.sort(key=lambda f: -f.count)
    return findings


def error_rate(events: list[ErrorEvent], requests: int) -> float:
    return len(events) / requests

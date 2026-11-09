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
    # Ids that mix letters and digits (ch_3MqLiJ2eZvKYlo2C, 3f4e1a9c2b7d,
    # req_8f3k2a91). <num> cannot match inside them because there is no word
    # boundary between a letter and a digit. Runs after <num> so "9000ms"
    # stays <num>; 8+ characters so short words like "utf8" survive.
    (re.compile(r"\b(?=\w*\d)(?=\w*[A-Za-z])\w{8,}\b"), "<id>"),
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


def _frame_without_line(top_frame: str) -> str:
    """Drop the trailing line number, so moving code does not change identity."""
    return re.sub(r":\d+$", "", top_frame)


def fingerprint(e: ErrorEvent) -> str:
    """Stable 12-hex-char id for the *kind* of error, not the instance.

    Line numbers are stripped from the frame so a deploy that only moves code
    does not make every known error look new.
    """
    key = "\x1f".join([e.service, e.exc_type, template(e.message), _frame_without_line(e.top_frame)])
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def coarse_key(e: ErrorEvent) -> str:
    """Service, exception type and frame only: the message is ignored.

    Used as a safety net for events whose message still has an unmasked
    variable part, which would otherwise give each event its own fingerprint.
    """
    key = "\x1f".join(["coarse", e.service, e.exc_type, _frame_without_line(e.top_frame)])
    return "c-" + hashlib.sha256(key.encode()).hexdigest()[:10]


@dataclass
class Finding:
    fingerprint: str
    kind: str                   # "new" | "surging"
    count: int
    baseline_count: int
    rate_ratio: float | None    # post rate / baseline rate (None if new)
    example: str
    grouped: bool = False       # True: found by the below-min_count safety net


def _example(e: ErrorEvent) -> str:
    return f"{e.service} {e.exc_type}: {template(e.message)}"


def _classify(key: str, count: int, baseline_count: int, baseline_requests: int,
              after_requests: int, surge_ratio: float, example: str,
              grouped: bool) -> Finding | None:
    """Return a "new" or "surging" finding for one group, or None if normal."""
    if baseline_count == 0:
        return Finding(key, "new", count, 0, None, example, grouped)
    ratio = (count / after_requests) / (baseline_count / baseline_requests)
    if ratio >= surge_ratio:
        return Finding(key, "surging", count, baseline_count, round(ratio, 2), example, grouped)
    return None


def compare_windows(baseline: list[ErrorEvent], baseline_requests: int,
                    after: list[ErrorEvent], after_requests: int, *,
                    min_count: int = 5, surge_ratio: float = 3.0) -> list[Finding]:
    """Findings sorted by count, largest first.

    ``min_count`` keeps one-off errors from paging anyone; ``surge_ratio``
    compares rates per request, so a traffic increase alone is not a surge.

    Safety net: events whose fingerprint stays below ``min_count`` are pooled
    by ``coarse_key``. If a message has an id the masks miss, every event gets
    its own fingerprint; without pooling, hundreds of them would each be a
    "one-off" and the new error would never be reported.
    """
    if baseline_requests <= 0 or after_requests <= 0:
        raise ValueError("request counts must be positive")
    base = Counter(fingerprint(e) for e in baseline)
    post: Counter[str] = Counter()
    examples: dict[str, str] = {}
    coarse_keys: dict[str, str] = {}
    for e in after:
        fp = fingerprint(e)
        post[fp] += 1
        examples.setdefault(fp, _example(e))
        coarse_keys.setdefault(fp, coarse_key(e))

    findings = []
    reported_coarse_keys = set()
    for fp, n in post.items():
        if n >= min_count:
            finding = _classify(fp, n, base.get(fp, 0), baseline_requests,
                                after_requests, surge_ratio, examples[fp], False)
            if finding:
                findings.append(finding)
                reported_coarse_keys.add(coarse_keys[fp])
    # Leftovers of an error already reported (e.g. a few ids the masks
    # missed) would only repeat that finding, so they are not reported again.
    for pooled in _pooled_rare_findings(baseline, after, post, baseline_requests,
                                        after_requests, min_count, surge_ratio):
        if pooled.fingerprint not in reported_coarse_keys:
            findings.append(pooled)
    findings.sort(key=lambda f: -f.count)
    return findings


def _pooled_rare_findings(baseline: list[ErrorEvent], after: list[ErrorEvent],
                          post: Counter[str], baseline_requests: int,
                          after_requests: int, min_count: int,
                          surge_ratio: float) -> list[Finding]:
    """Pool the after-window events whose fingerprint is below min_count.

    The baseline side counts only events not already covered by a frequent
    fingerprint, so known, well-masked errors do not hide a new pooled group.
    """
    frequent = {fp for fp, n in post.items() if n >= min_count}
    rare_counts: Counter[str] = Counter()
    rare_examples: dict[str, str] = {}
    for e in after:
        if fingerprint(e) not in frequent:
            key = coarse_key(e)
            rare_counts[key] += 1
            rare_examples.setdefault(key, _example(e))
    base_counts: Counter[str] = Counter()
    for e in baseline:
        if fingerprint(e) not in frequent:
            base_counts[coarse_key(e)] += 1

    findings = []
    for key, n in rare_counts.items():
        if n >= min_count:
            finding = _classify(key, n, base_counts.get(key, 0), baseline_requests,
                                after_requests, surge_ratio, rare_examples[key], True)
            if finding:
                findings.append(finding)
    return findings


def error_rate(events: list[ErrorEvent], requests: int) -> float:
    """Errors per request in a window."""
    return len(events) / requests

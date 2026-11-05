"""Automated canary verdict: compare the canary against a *concurrent* baseline.

The verdict is one of PROMOTE, ROLLBACK or CONTINUE (not enough evidence yet).
Two checks run on each analysis interval:

1. Error rate: one-sided test "is canary worse than baseline?" - an exact
   binomial test when errors are few, the two-proportion z-test otherwise -
   plus a minimum relative effect so tiny-but-significant differences on
   huge traffic don't block every deploy.
2. Latency: canary p99 must stay within ``max_p99_ratio`` of baseline p99.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from statistics import NormalDist, quantiles
from typing import Sequence


class Verdict(str, Enum):
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    CONTINUE = "continue"


@dataclass
class Cohort:
    requests: int
    errors: int
    latencies_ms: Sequence[float] = ()

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0


@dataclass
class CanaryResult:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    p_value: float | None = None


def static_threshold_gate(canary: Cohort, max_error_rate: float = 0.01) -> Verdict:
    """The original gate: promote if the canary's error rate is under 1%."""
    return Verdict.PROMOTE if canary.error_rate < max_error_rate else Verdict.ROLLBACK


# Above this many total errors the normal approximation is accurate and the
# exact sum would be slow; below it the z-test badly underestimates p-values.
EXACT_TEST_MAX_ERRORS = 1_000


def exact_one_sided_p_value(base: Cohort, canary: Cohort) -> float:
    """Exact p-value: chance the canary gets at least this many of the errors.

    Conditional on the total error count k, under "same true rate" each error
    lands in the canary with probability equal to the canary's traffic share,
    so the canary's errors are Binomial(k, share). Sums are done in log space
    so large binomial coefficients cannot overflow a float.
    """
    total_errors = base.errors + canary.errors
    if total_errors == 0:
        return 1.0
    share = canary.requests / (base.requests + canary.requests)
    p_value = 0.0
    for errors_in_canary in range(canary.errors, total_errors + 1):
        log_term = (math.lgamma(total_errors + 1)
                    - math.lgamma(errors_in_canary + 1)
                    - math.lgamma(total_errors - errors_in_canary + 1)
                    + errors_in_canary * math.log(share)
                    + (total_errors - errors_in_canary) * math.log1p(-share))
        p_value += math.exp(log_term)
    return min(p_value, 1.0)


def one_sided_p_value(base: Cohort, canary: Cohort) -> float:
    """P(observing a canary error rate this much higher | both share one true rate)."""
    # The z-test is only valid with many errors; tiny counts need the exact test.
    if base.errors + canary.errors <= EXACT_TEST_MAX_ERRORS:
        return exact_one_sided_p_value(base, canary)
    return z_test_one_sided_p_value(base, canary)


def z_test_one_sided_p_value(base: Cohort, canary: Cohort) -> float:
    """Normal-approximation two-proportion z-test (fine for large error counts)."""
    n1, n2 = base.requests, canary.requests
    pooled = (base.errors + canary.errors) / (n1 + n2)
    if pooled in (0.0, 1.0):
        return 1.0
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (canary.error_rate - base.error_rate) / se
    return 1 - NormalDist().cdf(z)


def p99(xs: Sequence[float]) -> float:
    return quantiles(xs, n=100, method="inclusive")[98]


@dataclass
class CanaryAnalyzer:
    alpha: float = 0.01                 # false-rollback rate per check we accept
    min_requests: int = 2_000           # per cohort before any verdict
    min_relative_increase: float = 0.25  # ignore < +25% relative error increase
    max_p99_ratio: float = 1.2
    min_latency_samples: int = 500

    def analyze(self, base: Cohort, canary: Cohort) -> CanaryResult:
        if min(base.requests, canary.requests) < self.min_requests:
            return CanaryResult(Verdict.CONTINUE, ["not enough traffic yet"])
        reasons: list[str] = []
        p = one_sided_p_value(base, canary)
        rel = ((canary.error_rate - base.error_rate) / base.error_rate
               if base.error_rate else (math.inf if canary.errors else 0.0))
        if p < self.alpha and rel >= self.min_relative_increase:
            reasons.append(f"error rate {canary.error_rate:.3%} vs baseline "
                           f"{base.error_rate:.3%} (p={p:.2g})")
        if (len(base.latencies_ms) >= self.min_latency_samples
                and len(canary.latencies_ms) >= self.min_latency_samples):
            b, c = p99(base.latencies_ms), p99(canary.latencies_ms)
            if c > b * self.max_p99_ratio:
                reasons.append(f"p99 {c:.0f}ms vs baseline {b:.0f}ms")
        if reasons:
            return CanaryResult(Verdict.ROLLBACK, reasons, p)
        return CanaryResult(Verdict.PROMOTE, ["canary within tolerance"], p)

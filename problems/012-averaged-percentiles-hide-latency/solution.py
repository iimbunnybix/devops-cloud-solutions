"""Aggregate latency percentiles correctly across hosts and time windows.

Percentiles are not additive: the p99 of a fleet is **not** the average (or
traffic-weighted average) of each host's p99. A dashboard that averages
per-host p99s can show a healthy number while 1 in 20 requests is slow.

The fix is to keep *mergeable* data: a histogram of counts per latency bucket.
Histograms from many hosts or minutes add up bucket by bucket, and the
percentile is computed once, from the merged counts. ``LogHistogram`` uses
logarithmic buckets so the relative error of any percentile is bounded
(the same idea as HdrHistogram, DDSketch and Prometheus native histograms).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Dict, Iterable, List, Sequence


def exact_percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 100]) of raw samples."""
    if not values:
        raise ValueError("no samples")
    if not 0 <= q <= 100:
        raise ValueError("q must be within [0, 100]")
    s = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(s)))
    return s[rank - 1]


def average_of_percentiles(per_host: Dict[str, Sequence[float]], q: float) -> float:
    """What the old dashboard did: compute p99 per host, then average."""
    return sum(exact_percentile(v, q) for v in per_host.values()) / len(per_host)


def weighted_average_of_percentiles(per_host: Dict[str, Sequence[float]], q: float) -> float:
    """The first attempted fix: weight each host's p99 by its request count. Still wrong."""
    total = sum(len(v) for v in per_host.values())
    return sum(exact_percentile(v, q) * len(v) for v in per_host.values()) / total


# Smallest latency (seconds) that gets its own log bucket; anything at or
# below it is reported as 0. One microsecond is far below any real request.
DEFAULT_MIN_LATENCY_SECONDS = 1e-6


class LogHistogram:
    """Latency histogram with logarithmic buckets and bounded relative error.

    Bucket ``i`` covers ``(gamma**(i-1), gamma**i]`` where
    ``gamma = (1 + alpha) / (1 - alpha)``. Reporting a bucket's midpoint
    ``2 * gamma**i / (gamma + 1)`` is within ``alpha`` (relative) of every value
    in the bucket. Values at or below ``min_value`` share one bucket that is
    reported as 0. Latencies are in seconds, so the default floor is 1 µs:
    a 1 ms floor would report every sub-millisecond request (cache hits,
    health checks) as 0 ms, a 100% error.
    """

    def __init__(self, relative_accuracy: float = 0.01, min_value: float = DEFAULT_MIN_LATENCY_SECONDS) -> None:
        if not 0 < relative_accuracy < 1:
            raise ValueError("relative_accuracy must be in (0, 1)")
        self.alpha = relative_accuracy
        self.gamma = (1 + relative_accuracy) / (1 - relative_accuracy)
        self._log_gamma = math.log(self.gamma)
        self.min_value = min_value
        self.counts: Counter = Counter()
        self.zero_count = 0
        self.count = 0
        self.max = 0.0

    def _key(self, v: float) -> int:
        return math.ceil(math.log(v) / self._log_gamma)

    def _value(self, key: int) -> float:
        return 2 * self.gamma ** key / (self.gamma + 1)

    def add(self, value: float, n: int = 1) -> None:
        if value < 0 or math.isnan(value):
            raise ValueError("latency must be a non-negative number")
        if value <= self.min_value:
            self.zero_count += n
        else:
            self.counts[self._key(value)] += n
        self.count += n
        self.max = max(self.max, value)

    def merge(self, other: "LogHistogram") -> "LogHistogram":
        if other.gamma != self.gamma or other.min_value != self.min_value:
            raise ValueError("can only merge histograms with identical bucket layout")
        self.counts.update(other.counts)
        self.zero_count += other.zero_count
        self.count += other.count
        self.max = max(self.max, other.max)
        return self

    def percentile(self, q: float) -> float:
        """Nearest-rank percentile from the bucket counts."""
        if self.count == 0:
            raise ValueError("empty histogram")
        if not 0 <= q <= 100:
            raise ValueError("q must be within [0, 100]")
        rank = max(1, math.ceil(q / 100 * self.count))
        seen = self.zero_count
        if rank <= seen:
            return 0.0
        for key in sorted(self.counts):
            seen += self.counts[key]
            if seen >= rank:
                return min(self._value(key), self.max)
        return self.max

    def to_dict(self) -> Dict[str, object]:
        """Serializable form for shipping from hosts to the aggregator."""
        return {"alpha": self.alpha, "min": self.min_value, "zero": self.zero_count,
                "buckets": {str(k): c for k, c in self.counts.items()}, "max": self.max}

    @classmethod
    def from_dict(cls, d: Dict[str, object]) -> "LogHistogram":
        h = cls(d["alpha"], d["min"])  # type: ignore[arg-type]
        h.zero_count = d["zero"]       # type: ignore[assignment]
        h.counts = Counter({int(k): c for k, c in d["buckets"].items()})  # type: ignore[union-attr]
        h.count = h.zero_count + sum(h.counts.values())
        h.max = d["max"]               # type: ignore[assignment]
        return h


def merged_percentile(histograms: Iterable[LogHistogram], q: float) -> float:
    hs: List[LogHistogram] = list(histograms)
    if not hs:
        raise ValueError("no histograms")
    total = LogHistogram(hs[0].alpha, hs[0].min_value)
    for h in hs:
        total.merge(h)
    return total.percentile(q)

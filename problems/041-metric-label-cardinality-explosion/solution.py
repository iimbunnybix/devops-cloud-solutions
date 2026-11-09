"""Stop one unbounded metric label from taking down the metrics server.

A metrics backend such as Prometheus stores one time series per unique
combination of label values, and its memory grows with the number of series.
Adding a label like ``user_id`` to a request counter turns ~100 series into
tens of thousands, and the server runs out of memory for every team.

``GuardedRegistry`` fixes this in the client, before samples leave the
service: it keeps only allow-listed labels per metric, and once a metric has
used its series budget, new label combinations are folded into one
``__overflow__`` series. Totals stay correct; only the detail is lost.
"""
from __future__ import annotations

OVERFLOW_VALUE = "__overflow__"
DEFAULT_MAX_SERIES_PER_METRIC = 500

Labels = dict[str, str]
SeriesKey = tuple[str, tuple[tuple[str, str], ...]]


def series_key(metric: str, labels: Labels) -> SeriesKey:
    """One hashable key per (metric, label set); label order does not matter."""
    return (metric, tuple(sorted(labels.items())))


class NaiveRegistry:
    """The original client: every new label combination becomes a new series."""

    def __init__(self) -> None:
        self.values: dict[SeriesKey, float] = {}

    def inc(self, metric: str, labels: Labels, amount: float = 1.0) -> None:
        """Add ``amount`` to the counter for exactly these labels."""
        key = series_key(metric, labels)
        self.values[key] = self.values.get(key, 0.0) + amount

    def series_count(self) -> int:
        """Number of time series the metrics server would have to keep."""
        return len(self.values)

    def total(self, metric: str) -> float:
        """Sum of a counter over all its series."""
        return sum(v for (name, _), v in self.values.items() if name == metric)


class GuardedRegistry(NaiveRegistry):
    """Counter registry with an allow-list of labels and a per-metric budget."""

    def __init__(self, allowed_labels: dict[str, set[str]],
                 max_series_per_metric: int = DEFAULT_MAX_SERIES_PER_METRIC) -> None:
        super().__init__()
        if max_series_per_metric < 1:
            raise ValueError("max_series_per_metric must be at least 1")
        self.allowed_labels = allowed_labels
        self.max_series_per_metric = max_series_per_metric
        self.series_per_metric: dict[str, int] = {}
        self.dropped_label_names: set[tuple[str, str]] = set()
        self.overflowed_samples: dict[str, int] = {}

    def inc(self, metric: str, labels: Labels, amount: float = 1.0) -> None:
        """Add ``amount`` after filtering labels and enforcing the budget."""
        kept = self._keep_allowed_labels(metric, labels)
        key = series_key(metric, kept)
        if key not in self.values and self._budget_used(metric):
            # WHY: a new series would exceed the budget. Count the sample in
            # the overflow series so totals and error rates stay correct.
            key = series_key(metric, self._overflow_labels(kept))
            self.overflowed_samples[metric] = self.overflowed_samples.get(metric, 0) + 1
        if key not in self.values:
            self.series_per_metric[metric] = self.series_per_metric.get(metric, 0) + 1
        self.values[key] = self.values.get(key, 0.0) + amount

    def _keep_allowed_labels(self, metric: str, labels: Labels) -> Labels:
        """Drop labels that are not on the metric's allow-list and remember them."""
        allowed = self.allowed_labels.get(metric, set())
        kept: Labels = {}
        for name, value in labels.items():
            if name in allowed:
                kept[name] = value
            else:
                self.dropped_label_names.add((metric, name))
        return kept

    def _budget_used(self, metric: str) -> bool:
        """True when the metric already has its maximum number of real series.

        One slot is kept free for the overflow series itself.
        """
        return self.series_per_metric.get(metric, 0) >= self.max_series_per_metric - 1

    @staticmethod
    def _overflow_labels(labels: Labels) -> Labels:
        """Same label names, every value replaced by the overflow marker."""
        return {name: OVERFLOW_VALUE for name in labels}

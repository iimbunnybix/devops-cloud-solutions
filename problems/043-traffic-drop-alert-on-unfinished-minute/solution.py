"""Alert on a traffic drop only over minutes whose data has fully arrived.

The "checkout traffic dropped" alert counted requests in the minute that had
just ended. Hosts ship their request counts in batches every 15 seconds and
each batch takes a few more seconds to arrive, so that minute was always
missing its last 13-25 seconds and looked like a drop. The alert fired about
every other minute and was ignored. This module tracks an ingestion watermark
(the time up to which every host's data has arrived) and checks the newest
minute that ends before it. If the watermark falls too far behind, it reports
"data delayed" instead of pretending traffic fell.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

SECONDS_PER_MINUTE = 60
DROP_RATIO = 0.7          # alert when a minute has under 70% of normal traffic
MAX_DATA_DELAY_S = 180.0  # a watermark older than this means the pipeline is stuck


class Verdict(Enum):
    OK = "ok"
    TRAFFIC_DROP = "traffic_drop"
    DATA_DELAYED = "data_delayed"


@dataclass(frozen=True)
class Batch:
    """One shipment of request counts from one host.

    ``counts_by_second`` maps an event-time second to the requests served in
    it. The shipper promises that every second before ``covers_until`` is in
    this batch or an earlier one.
    """
    host: str
    covers_until: float
    arrived_at: float
    counts_by_second: dict[int, int]


class RequestCountStore:
    """The metrics store: batches become visible once they have arrived."""

    def __init__(self, hosts: list[str]) -> None:
        self.hosts = hosts
        self.batches: list[Batch] = []

    def add(self, batch: Batch) -> None:
        """Record a batch (it is only visible to queries after ``arrived_at``)."""
        self.batches.append(batch)

    def count_between(self, start: float, end: float, as_of: float) -> int:
        """Requests with event time in [start, end) that had arrived by ``as_of``."""
        total = 0
        for batch in self.batches:
            if batch.arrived_at > as_of:
                continue
            for second, count in batch.counts_by_second.items():
                if start <= second < end:
                    total += count
        return total

    def host_watermark(self, host: str, as_of: float) -> float:
        """Latest time up to which all of ``host``'s data had arrived by ``as_of``.

        Batches can overtake each other in transit, so a late batch only moves
        the watermark once every batch before it has arrived too.
        """
        own_batches = [batch for batch in self.batches if batch.host == host]
        own_batches.sort(key=lambda batch: batch.covers_until)
        watermark = float("-inf")
        for batch in own_batches:
            if batch.arrived_at > as_of:
                break
            watermark = batch.covers_until
        return watermark

    def watermark(self, as_of: float) -> float:
        """Time up to which data from EVERY host is complete (the slowest host)."""
        slowest = float("inf")
        for host in self.hosts:
            slowest = min(slowest, self.host_watermark(host, as_of))
        return slowest


def naive_traffic_check(store: RequestCountStore, now: float,
                        expected_per_minute: float) -> Verdict:
    """Original alert: count the minute that just ended, complete or not."""
    count = store.count_between(now - SECONDS_PER_MINUTE, now, as_of=now)
    if count < DROP_RATIO * expected_per_minute:
        return Verdict.TRAFFIC_DROP
    return Verdict.OK


def last_complete_minute_end(watermark: float) -> float:
    """End of the newest whole minute that lies entirely before the watermark."""
    whole_minutes = watermark // SECONDS_PER_MINUTE
    return whole_minutes * SECONDS_PER_MINUTE


def watermark_traffic_check(store: RequestCountStore, now: float,
                            expected_per_minute: float) -> Verdict:
    """Fixed alert: judge traffic only on a minute whose data is complete."""
    minute_end = last_complete_minute_end(store.watermark(as_of=now))
    # If even the newest complete minute is old, the shippers or the pipeline
    # are stuck. That needs attention, but it is not a traffic drop.
    if now - minute_end > MAX_DATA_DELAY_S:
        return Verdict.DATA_DELAYED
    count = store.count_between(minute_end - SECONDS_PER_MINUTE, minute_end, as_of=now)
    if count < DROP_RATIO * expected_per_minute:
        return Verdict.TRAFFIC_DROP
    return Verdict.OK

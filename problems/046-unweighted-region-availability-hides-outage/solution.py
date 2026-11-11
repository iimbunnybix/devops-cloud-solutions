"""Fleet availability computed from request counts, not averaged percentages.

The status dashboard showed "global availability" as the plain average of each
region's success percentage. Every region counted the same, so a total outage
in the region serving most traffic looked like a small dip, while a few errors
in a tiny region looked like a crisis.

The fix sums good and total requests across regions and divides once, which
weights every region by its traffic, and separately lists each region whose
own availability is below the SLO so a regional outage is never averaged away.
"""
from __future__ import annotations

from dataclasses import dataclass

SLO_TARGET = 0.999  # 99.9% of requests must succeed


@dataclass(frozen=True)
class RegionStats:
    """Request counts for one region over the reporting window."""

    region: str
    good_requests: int
    total_requests: int


@dataclass(frozen=True)
class AvailabilityReport:
    """What the dashboard shows: one fleet number plus the regions in breach."""

    fleet_availability: float | None
    meets_slo: bool
    regions_breaching_slo: list[str]
    regions_without_traffic: list[str]


def region_availability(stats: RegionStats) -> float | None:
    """Success ratio for one region, or None if it served no requests."""
    if stats.good_requests < 0 or stats.good_requests > stats.total_requests:
        raise ValueError(f"{stats.region}: good requests must be between 0 and total")
    if stats.total_requests == 0:
        # No traffic means "unknown", not 100% or 0%.
        return None
    return stats.good_requests / stats.total_requests


def naive_fleet_availability(regions: list[RegionStats]) -> float:
    """Original dashboard: average the per-region percentages.

    A region with 1,000 requests has the same weight as one with 10 million.
    """
    ratios = []
    for stats in regions:
        ratio = region_availability(stats)
        if ratio is not None:
            ratios.append(ratio)
    return sum(ratios) / len(ratios)


def fleet_availability(regions: list[RegionStats]) -> float | None:
    """Good requests over total requests across all regions (traffic-weighted)."""
    good = 0
    total = 0
    for stats in regions:
        region_availability(stats)  # validates the counts
        good += stats.good_requests
        total += stats.total_requests
    if total == 0:
        return None
    return good / total


def regions_below_slo(regions: list[RegionStats], target: float) -> list[str]:
    """Regions whose own availability misses the target, worst first."""
    breaching = []
    for stats in regions:
        ratio = region_availability(stats)
        if ratio is not None and ratio < target:
            breaching.append((ratio, stats.region))
    breaching.sort()
    names = []
    for _, region in breaching:
        names.append(region)
    return names


def availability_report(regions: list[RegionStats], target: float = SLO_TARGET) -> AvailabilityReport:
    """Build the fixed dashboard report."""
    overall = fleet_availability(regions)
    breaching = regions_below_slo(regions, target)
    silent = []
    for stats in regions:
        if stats.total_requests == 0:
            silent.append(stats.region)
    # The fleet only meets the SLO if the weighted number does AND no single
    # region is in breach: users in the broken region do not care about the average.
    meets = overall is not None and overall >= target and not breaching
    return AvailabilityReport(overall, meets, breaching, silent)

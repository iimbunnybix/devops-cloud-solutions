"""Find and safely clean up orphaned cloud resources (unattached volumes, stale snapshots, idle IPs).

Two problems motivated this:

1. **The leak.** Ephemeral CI environments were torn down by deleting the
   instances only. Their data volumes (created with delete-on-termination off),
   the snapshots taken of them, and their reserved public IPs kept billing.
2. **The over-eager first fix.** A script that deleted *every* unattached
   volume also deleted a production volume that had been detached minutes
   earlier for a planned maintenance.

``find_orphans`` applies age thresholds and exemptions; ``CleanupJob`` works in
two phases: it first *marks* candidates (and reports owners/cost), then, after
a grace period, deletes only resources that are still orphaned and still
marked.

Prices are illustrative per-month rates for the simulation, not any
provider's price list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

# Illustrative monthly rates used by the simulation and the report.
RATES = {
    "volume_gb_month": 0.08,
    "snapshot_gb_month": 0.05,
    "idle_ip_month": 3.60,
}

MARK_TAG = "cleanup:scheduled"


@dataclass
class Resource:
    id: str
    kind: str                         # "volume" | "snapshot" | "ip"
    created_at: datetime
    size_gb: int = 0
    attached_to: Optional[str] = None  # instance id for volumes / ips
    detached_at: Optional[datetime] = None
    source_volume: Optional[str] = None  # snapshots only
    tags: dict = field(default_factory=dict)

    def monthly_cost(self) -> float:
        if self.kind == "volume":
            return self.size_gb * RATES["volume_gb_month"]
        if self.kind == "snapshot":
            return self.size_gb * RATES["snapshot_gb_month"]
        if self.kind == "ip":
            return RATES["idle_ip_month"] if self.attached_to is None else 0.0
        raise ValueError(f"unknown kind {self.kind}")


@dataclass
class Policy:
    volume_unattached_days: int = 14
    snapshot_orphan_days: int = 90
    ip_unassociated_days: int = 7
    grace_days: int = 7


@dataclass
class Finding:
    resource: Resource
    reason: str
    monthly_cost: float


class Inventory:
    """In-memory stand-in for the cloud API."""

    def __init__(self, resources: list[Resource]) -> None:
        self.items = {r.id: r for r in resources}
        self.deleted: list[str] = []

    def all(self) -> list[Resource]:
        return list(self.items.values())

    def delete(self, rid: str) -> None:
        self.deleted.append(rid)
        del self.items[rid]


def naive_cleanup(inv: Inventory) -> list[str]:
    """The first fix: delete everything unattached, right now."""
    victims = [r.id for r in inv.all() if r.kind in ("volume", "ip") and r.attached_to is None]
    for rid in victims:
        inv.delete(rid)
    return victims


def _exempt(r: Resource, now: datetime) -> Optional[str]:
    if str(r.tags.get("keep", "")).lower() == "true":
        return "tagged keep=true"
    expires = r.tags.get("expires")
    if expires:
        try:
            if datetime.fromisoformat(expires) > now:
                return f"expires {expires}"
        except ValueError:
            pass  # malformed tag: do not treat as an exemption
    return None


def find_orphans(inv: Inventory, now: datetime, policy: Policy = Policy()) -> list[Finding]:
    """Return orphaned resources, most expensive first."""
    volume_ids = {r.id for r in inv.all() if r.kind == "volume"}
    out: list[Finding] = []
    for r in inv.all():
        if _exempt(r, now):
            continue
        reason = None
        if r.kind == "volume" and r.attached_to is None:
            since = r.detached_at or r.created_at
            days = (now - since).days
            if days >= policy.volume_unattached_days:
                reason = f"volume unattached for {days} days"
        elif r.kind == "snapshot" and r.source_volume not in volume_ids:
            days = (now - r.created_at).days
            if days >= policy.snapshot_orphan_days:
                reason = f"snapshot of deleted volume, {days} days old"
        elif r.kind == "ip" and r.attached_to is None:
            since = r.detached_at or r.created_at
            days = (now - since).days
            if days >= policy.ip_unassociated_days:
                reason = f"IP unassociated for {days} days"
        if reason:
            out.append(Finding(r, reason, round(r.monthly_cost(), 2)))
    out.sort(key=lambda f: (-f.monthly_cost, f.resource.id))
    return out


def cost_by_owner(findings: list[Finding]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for f in findings:
        owner = f.resource.tags.get("team", "untagged")
        totals[owner] = round(totals.get(owner, 0.0) + f.monthly_cost, 2)
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


class CleanupJob:
    """Two-phase cleanup: mark now, delete after ``grace_days`` if still orphaned."""

    def __init__(self, inv: Inventory, policy: Policy = Policy(), dry_run: bool = True) -> None:
        self.inv = inv
        self.policy = policy
        self.dry_run = dry_run

    def mark(self, now: datetime) -> list[Finding]:
        findings = find_orphans(self.inv, now, self.policy)
        for f in findings:
            f.resource.tags.setdefault(MARK_TAG, now.date().isoformat())
        # unmark anything that is no longer an orphan (re-attached, exempted)
        orphan_ids = {f.resource.id for f in findings}
        for r in self.inv.all():
            if MARK_TAG in r.tags and r.id not in orphan_ids:
                del r.tags[MARK_TAG]
        return findings

    def sweep(self, now: datetime) -> list[str]:
        """Delete resources marked at least ``grace_days`` ago that are still orphaned."""
        still_orphaned = {f.resource.id for f in find_orphans(self.inv, now, self.policy)}
        due = []
        for r in self.inv.all():
            mark = r.tags.get(MARK_TAG)
            if not mark or r.id not in still_orphaned:
                continue
            if now - datetime.fromisoformat(mark) >= timedelta(days=self.policy.grace_days):
                due.append(r.id)
        if not self.dry_run:
            for rid in due:
                self.inv.delete(rid)
        return sorted(due)

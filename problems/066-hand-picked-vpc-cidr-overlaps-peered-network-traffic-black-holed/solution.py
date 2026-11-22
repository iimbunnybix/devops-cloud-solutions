"""Allocate new VPC/subnet ranges that never overlap networks we route to.

A new analytics VPC was given ``10.20.0.0/16`` by hand, copied from a wiki
page. The office VPN already used ``10.20.8.0/21`` and a partner's peered VPC
used ``10.20.64.0/18``. Once the VPC was peered, its own local route covered
those ranges, so packets meant for the VPN and partner stayed inside the new
VPC and went nowhere: connections to them timed out.

The fix keeps one registry of every range in use (own VPCs, peered VPCs, VPN
and on-prem networks), validates any proposed CIDR against it, and replaces
hand-picking with an allocator that returns the first free block of the
requested size inside the company supernet.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

Network = ipaddress.IPv4Network


@dataclass(frozen=True)
class UsedRange:
    cidr: Network
    owner: str        # who uses it: "vpc:payments", "vpn:office", "peer:partner"


class AllocationError(Exception):
    """No free block of the requested size is left in the supernet."""


def parse_network(text: str) -> Network:
    """Parse a CIDR strictly: '10.20.1.0/16' (host bits set) is rejected.

    A typo like that usually means the author meant a different range, so
    we refuse it instead of silently rounding it to 10.20.0.0/16.
    """
    return ipaddress.IPv4Network(text, strict=True)


def naive_accept(proposed: str, used: list[UsedRange]) -> bool:
    """The original process: only check that nobody uses the *same* CIDR."""
    proposed_network = parse_network(proposed)
    for existing in used:
        if existing.cidr == proposed_network:
            return False
    return True


def find_overlaps(proposed: Network, used: list[UsedRange]) -> list[UsedRange]:
    """Every used range that shares at least one address with ``proposed``.

    ``overlaps`` is true for partial overlap and for containment in either
    direction, which an equality check misses.
    """
    overlapping = []
    for existing in used:
        if proposed.overlaps(existing.cidr):
            overlapping.append(existing)
    return overlapping


def validate(proposed: str, used: list[UsedRange]) -> list[str]:
    """Human-readable problems with a proposed CIDR; empty means it is safe."""
    proposed_network = parse_network(proposed)
    problems = []
    for existing in find_overlaps(proposed_network, used):
        problems.append(f"{proposed_network} overlaps {existing.cidr} ({existing.owner})")
    return problems


def allocate(supernet: str, prefix_length: int, used: list[UsedRange]) -> Network:
    """First block of ``/prefix_length`` inside ``supernet`` that overlaps nothing.

    Walking aligned candidate blocks in address order is deterministic, so
    two engineers asking for the same size get the same answer, and the
    lowest free space fills first, which keeps big blocks free for later.
    """
    parent = parse_network(supernet)
    if prefix_length < parent.prefixlen:
        raise ValueError(f"/{prefix_length} is larger than the supernet {parent}")
    for candidate in parent.subnets(new_prefix=prefix_length):
        if not find_overlaps(candidate, used):
            return candidate
    raise AllocationError(f"no free /{prefix_length} left in {parent}")


def reserve(supernet: str, prefix_length: int, owner: str, used: list[UsedRange]) -> Network:
    """Allocate a block and record it, so the next allocation skips it."""
    block = allocate(supernet, prefix_length, used)
    used.append(UsedRange(block, owner))
    return block

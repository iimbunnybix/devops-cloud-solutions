"""Stop "remove one queue" from destroying and recreating every later queue.

The messaging stack declared its queues as one list and created them with a
count, so each queue's address was its position: ``queue[0]``, ``queue[1]``...
Removing one queue from the middle of the list shifted every later queue down
one position. The planner compares old and new resources by address, so it saw
changed names at every shifted position and planned destroy + create for all
of them, deleting their unread messages.

The fix addresses each resource by a stable key (its name, like Terraform's
``for_each``) so removing one item touches only that item, and adds a guard
that blocks any plan which would destroy a protected resource.
"""
from __future__ import annotations

from dataclasses import dataclass

CREATE = "create"
DESTROY = "destroy"
# Attributes that cannot change in place: changing them means destroy + create.
FORCE_NEW_ATTRIBUTES = ("name",)


@dataclass(frozen=True)
class Change:
    address: str      # e.g. "queue[2]" or 'queue["billing"]'
    action: str       # CREATE or DESTROY
    name: str         # the real queue name affected


class PlanBlocked(Exception):
    """The plan would destroy resources marked as protected."""


def index_addresses(queue_names: list[str]) -> dict[str, str]:
    """count-style addressing: the address is the list position."""
    addresses = {}
    for position, name in enumerate(queue_names):
        addresses[f"queue[{position}]"] = name
    return addresses


def key_addresses(queue_names: list[str]) -> dict[str, str]:
    """for_each-style addressing: the address is the queue's own name."""
    if len(set(queue_names)) != len(queue_names):
        raise ValueError("queue names must be unique to be used as keys")
    addresses = {}
    for name in queue_names:
        addresses[f'queue["{name}"]'] = name
    return addresses


def diff(current: dict[str, str], desired: dict[str, str]) -> list[Change]:
    """Compare resources address by address, the way an IaC planner does."""
    changes: list[Change] = []
    for address, current_name in current.items():
        desired_name = desired.get(address)
        if desired_name is None:
            changes.append(Change(address, DESTROY, current_name))
        elif desired_name != current_name:
            # WHY: the name is force-new, so a different name at the same
            # address is a replacement, not an in-place rename.
            changes.append(Change(address, DESTROY, current_name))
            changes.append(Change(address, CREATE, desired_name))
    for address, desired_name in desired.items():
        if address not in current:
            changes.append(Change(address, CREATE, desired_name))
    return changes


def naive_plan(current_names: list[str], desired_names: list[str]) -> list[Change]:
    """The original setup: resources created with count, addressed by index."""
    return diff(index_addresses(current_names), index_addresses(desired_names))


def keyed_plan(current_names: list[str], desired_names: list[str]) -> list[Change]:
    """The fix: resources addressed by their stable name."""
    return diff(key_addresses(current_names), key_addresses(desired_names))


def destroyed_names(plan: list[Change]) -> list[str]:
    """Names of every real resource the plan would delete."""
    names = []
    for change in plan:
        if change.action == DESTROY:
            names.append(change.name)
    return names


def check_plan(plan: list[Change], protected: set[str], approved: set[str]) -> None:
    """Block the plan if it destroys a protected resource nobody approved.

    The guard is a second line of defence: even with keyed addresses, a
    refactor can still produce surprising destroys, and a queue holding
    unread messages should never disappear without an explicit approval.
    """
    blocked = []
    for name in destroyed_names(plan):
        if name in protected and name not in approved:
            blocked.append(name)
    if blocked:
        raise PlanBlocked("plan destroys protected resources: " + ", ".join(sorted(blocked)))


def count_actions(plan: list[Change]) -> dict[str, int]:
    """Summary like a planner prints: how many creates and destroys."""
    summary = {CREATE: 0, DESTROY: 0}
    for change in plan:
        summary[change.action] += 1
    return summary

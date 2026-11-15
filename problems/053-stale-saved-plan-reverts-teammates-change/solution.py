"""Refuse to apply a saved infrastructure plan once the state has moved on.

A plan computed from the state at 10:00 and applied at 11:00 still carries the
"after" values it computed at 10:00. If a teammate applied a change in between
(an emergency scale-up, say), applying the old plan silently writes the old
values back and undoes their work. This module stamps every plan with the
state's lineage, serial and a hash of the resources it read, and the safe
apply refuses a plan whose stamp no longer matches the current state.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"


class StalePlanError(Exception):
    """The plan was made from an older state and must be re-planned."""


@dataclass
class State:
    """The recorded state of real infrastructure, like a Terraform state file.

    lineage identifies one state history; serial goes up on every write, so
    two states with the same lineage and serial are the same version.
    """
    lineage: str
    serial: int = 0
    resources: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Change:
    """One planned change: the full attribute set before and after."""
    resource_name: str
    action: str
    before: dict[str, str] | None
    after: dict[str, str] | None


@dataclass(frozen=True)
class Plan:
    """A saved plan plus the stamp of the state it was computed from."""
    changes: tuple[Change, ...]
    state_lineage: str
    state_serial: int
    resources_hash: str


def hash_resources(resources: dict[str, dict[str, str]]) -> str:
    """Stable fingerprint of resource attributes (key order does not matter)."""
    canonical = json.dumps(resources, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_resource(name: str, current: dict[str, str] | None,
                  desired: dict[str, str] | None) -> Change | None:
    """Return the change needed to turn current into desired, or None."""
    if current == desired:
        return None
    if current is None:
        return Change(name, ACTION_CREATE, None, dict(desired or {}))
    if desired is None:
        return Change(name, ACTION_DELETE, dict(current), None)
    return Change(name, ACTION_UPDATE, dict(current), dict(desired))


def make_plan(state: State, desired: dict[str, dict[str, str]]) -> Plan:
    """Compare the desired config with the state and stamp the result."""
    changes: list[Change] = []
    for name in sorted(set(state.resources) | set(desired)):
        change = diff_resource(name, state.resources.get(name), desired.get(name))
        if change is not None:
            changes.append(change)
    return Plan(tuple(changes), state.lineage, state.serial,
                hash_resources(state.resources))


def write_changes(state: State, plan: Plan) -> None:
    """Write each change's full 'after' value into the state, bumping serial."""
    for change in plan.changes:
        if change.after is None:
            state.resources.pop(change.resource_name, None)
        else:
            state.resources[change.resource_name] = dict(change.after)
    state.serial += 1


def naive_apply(state: State, plan: Plan) -> None:
    """The original pipeline step: apply whatever plan file it is given (the bug).

    The plan's 'after' values were computed from an older state, so any
    attribute a teammate changed since then is overwritten with the old value.
    """
    write_changes(state, plan)


def check_plan_is_current(state: State, plan: Plan) -> None:
    """Raise StalePlanError unless the state is exactly the one planned against."""
    if plan.state_lineage != state.lineage:
        raise StalePlanError(
            f"plan is for state lineage {plan.state_lineage}, current is {state.lineage}")
    if plan.state_serial != state.serial:
        raise StalePlanError(
            f"plan was made at serial {plan.state_serial}, state is now at "
            f"{state.serial}; run plan again")
    # The serial catches normal writes; the hash also catches a state that
    # was edited by hand or restored from a backup without bumping the serial.
    if plan.resources_hash != hash_resources(state.resources):
        raise StalePlanError("state contents changed since the plan was made")


def safe_apply(state: State, plan: Plan) -> None:
    """Apply the plan only if nothing has changed the state since planning."""
    check_plan_is_current(state, plan)
    write_changes(state, plan)

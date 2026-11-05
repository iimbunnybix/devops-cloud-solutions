"""Stop an infrastructure-as-code rename from destroying a production database.

Terraform-style tools track each resource by its *address* (for example
``aws_db_instance.main``). Renaming the address in code, or changing an
attribute the provider cannot update in place, makes the plan *delete* the
existing resource and *create* a new, empty one. The original pipeline
auto-applied any plan that had no errors, so a rename wiped a database.

This module reviews a plan before apply. It blocks deletes and replacements
of stateful resource types, and it recognises the rename pattern (a delete
and a create of the same type with the same configuration) so it can tell
the engineer to declare a ``moved`` block instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Resource types whose loss means data loss, not just a few minutes of rebuild.
STATEFUL_TYPES = frozenset({
    "aws_db_instance", "aws_rds_cluster", "aws_dynamodb_table",
    "aws_s3_bucket", "aws_ebs_volume", "aws_elasticache_cluster",
})

# Attributes the provider fills in after creation. They differ between the
# old and new resource even when the configuration is identical, so they are
# ignored when deciding whether a delete + create is really a rename.
COMPUTED_ATTRIBUTES = frozenset({"id", "arn", "endpoint", "created_at"})

DELETE = "delete"
CREATE = "create"


@dataclass
class ResourceChange:
    """One entry of a plan, shaped like ``terraform show -json`` resource_changes."""
    address: str
    resource_type: str
    actions: list[str]
    before: dict | None = None
    after: dict | None = None

    @property
    def is_delete_only(self) -> bool:
        return self.actions == [DELETE]

    @property
    def is_create_only(self) -> bool:
        return self.actions == [CREATE]

    @property
    def is_replace(self) -> bool:
        # A replacement is planned as delete+create in either order.
        return DELETE in self.actions and CREATE in self.actions


@dataclass
class Finding:
    """A reason the plan must not be auto-applied."""
    address: str
    kind: str          # "rename", "replace" or "destroy"
    message: str


@dataclass
class Review:
    """Result of reviewing a plan."""
    findings: list[Finding] = field(default_factory=list)

    @property
    def safe_to_auto_apply(self) -> bool:
        return not self.findings


def naive_can_auto_apply(changes: list[ResourceChange], plan_errors: list[str]) -> bool:
    """Original pipeline gate: apply whenever the plan itself produced no errors."""
    return not plan_errors


def configuration_of(attributes: dict | None) -> dict:
    """Return the user-controlled part of a resource's attributes."""
    if attributes is None:
        return {}
    config = {}
    for name, value in attributes.items():
        if name not in COMPUTED_ATTRIBUTES:
            config[name] = value
    return config


def find_renames(changes: list[ResourceChange]) -> dict[str, str]:
    """Map old address -> new address for delete/create pairs that look like renames.

    A pair counts as a rename only when both sides have the same type and the
    same configuration, and each side is matched at most once.
    """
    creates = [change for change in changes if change.is_create_only]
    used_new_addresses: set[str] = set()
    renames: dict[str, str] = {}
    for deleted in changes:
        if not deleted.is_delete_only:
            continue
        old_config = configuration_of(deleted.before)
        for created in creates:
            if created.address in used_new_addresses:
                continue
            same_type = created.resource_type == deleted.resource_type
            if same_type and configuration_of(created.after) == old_config:
                renames[deleted.address] = created.address
                used_new_addresses.add(created.address)
                break
    return renames


def changed_attributes(change: ResourceChange) -> list[str]:
    """Names of configuration attributes whose value differs before vs after."""
    before = configuration_of(change.before)
    after = configuration_of(change.after)
    names = sorted(set(before) | set(after))
    return [name for name in names if before.get(name) != after.get(name)]


def review_plan(changes: list[ResourceChange],
                stateful_types: frozenset[str] = STATEFUL_TYPES) -> Review:
    """Return findings for every change that would lose data in a stateful resource."""
    review = Review()
    renames = find_renames(changes)
    for change in changes:
        if change.resource_type not in stateful_types:
            continue
        if change.address in renames:
            new_address = renames[change.address]
            review.findings.append(Finding(change.address, "rename", (
                f"looks like a rename to {new_address}; add "
                f"moved {{ from = {change.address}  to = {new_address} }} instead")))
        elif change.is_replace:
            attributes = ", ".join(changed_attributes(change)) or "unknown attribute"
            review.findings.append(Finding(change.address, "replace", (
                f"would be destroyed and recreated because of: {attributes}")))
        elif change.is_delete_only:
            review.findings.append(Finding(change.address, "destroy", "would be destroyed"))
    return review

"""Block database migrations that would make a rollback impossible.

The deploy pipeline ran the schema migration, then rolled out the new app
version. Its safety check only asked "does the NEW version work with the NEW
schema?". Release 42 renamed a column in the same deploy. The release had an
unrelated bug, the team rolled the app back to release 41, and release 41
crashed on every request because the column it used no longer existed. A
two-minute rollback became a 50-minute outage.

The fix adds the backward check: the migration must also leave a schema that
the PREVIOUS app version can still run against (expand/contract). Dropping
or renaming a column the old version uses, or adding a required column the
old version does not fill in, fails the gate before anything is applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Schema = dict[str, dict[str, "Column"]]


@dataclass(frozen=True)
class Column:
    name: str
    nullable: bool = True
    has_default: bool = False

    def required_on_insert(self) -> bool:
        """An INSERT that omits this column fails."""
        return not self.nullable and not self.has_default


@dataclass(frozen=True)
class Operation:
    kind: str                 # add_column | drop_column | rename_column
    table: str
    column: Column
    new_name: str = ""        # only for rename_column


@dataclass
class AppVersion:
    """Which columns a release reads and which it writes on INSERT."""
    name: str
    reads: dict[str, set[str]] = field(default_factory=dict)
    inserts: dict[str, set[str]] = field(default_factory=dict)


def apply_migration(schema: Schema, operations: list[Operation]) -> Schema:
    """Return the schema after the migration (input is not modified)."""
    result = {table: dict(columns) for table, columns in schema.items()}
    for operation in operations:
        columns = result.setdefault(operation.table, {})
        if operation.kind == "add_column":
            columns[operation.column.name] = operation.column
        elif operation.kind == "drop_column":
            columns.pop(operation.column.name, None)
        elif operation.kind == "rename_column":
            old = columns.pop(operation.column.name)
            columns[operation.new_name] = Column(operation.new_name, old.nullable, old.has_default)
        else:
            raise ValueError(f"unknown operation {operation.kind!r}")
    return result


def compatibility_problems(app: AppVersion, schema: Schema) -> list[str]:
    """Reasons ``app`` would fail against ``schema``; empty if it is fine."""
    problems = []
    for table, used_columns in sorted(app.reads.items()):
        existing = schema.get(table, {})
        for column_name in sorted(used_columns):
            if column_name not in existing:
                problems.append(f"{app.name} reads {table}.{column_name}, which does not exist")
    for table, inserted_columns in sorted(app.inserts.items()):
        for column in sorted(schema.get(table, {}).values(), key=lambda c: c.name):
            # The old release does not know about the new column, so every
            # INSERT it makes would violate NOT NULL.
            if column.required_on_insert() and column.name not in inserted_columns:
                problems.append(
                    f"{app.name} inserts into {table} without {column.name}, "
                    f"which is NOT NULL with no default"
                )
    return problems


def naive_deploy_gate(new_app: AppVersion, schema: Schema,
                      operations: list[Operation]) -> list[str]:
    """The original gate: only checks the new release against the new schema."""
    return compatibility_problems(new_app, apply_migration(schema, operations))


def rollback_safe_deploy_gate(old_app: AppVersion, new_app: AppVersion, schema: Schema,
                              operations: list[Operation]) -> list[str]:
    """Check both directions: the new release AND a rollback to the old one.

    The old release keeps running during a rolling deploy and is what you
    roll back to, so it must work against the migrated schema too.
    """
    migrated = apply_migration(schema, operations)
    problems = compatibility_problems(new_app, migrated)
    for problem in compatibility_problems(old_app, migrated):
        problems.append(f"rollback blocked: {problem}")
    return problems

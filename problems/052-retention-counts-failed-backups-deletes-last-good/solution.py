"""Backup retention that never deletes the last good backup.

The nightly database backup kept "the newest 7 backups" and deleted the rest.
When the backup job started failing every night (an expired storage
credential), each failed run still produced a backup record, so after a week
the 7 newest were all failures and retention deleted the last backup that
could actually be restored. This module counts only usable backups toward
the quota, never deletes a backup that is still running, and reports when
the newest usable backup is too old so the failures page someone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"
KEEP_USABLE_BACKUPS = 7
# Nightly backups: if the newest usable one is older than this, a night was missed.
MAX_USABLE_BACKUP_AGE = timedelta(hours=26)


@dataclass(frozen=True)
class Backup:
    name: str
    started_at: datetime
    status: str
    size_bytes: int


def start_time(backup: Backup) -> datetime:
    """Sort key: when the backup run started."""
    return backup.started_at


def newest_first(backups: list[Backup]) -> list[Backup]:
    """Backups sorted from newest to oldest by start time."""
    return sorted(backups, key=start_time, reverse=True)


def naive_backups_to_delete(backups: list[Backup], keep: int = KEEP_USABLE_BACKUPS) -> list[str]:
    """Original rule: keep the newest `keep` records of any status, delete the rest."""
    to_delete = []
    for backup in newest_first(backups)[keep:]:
        to_delete.append(backup.name)
    return to_delete


def is_usable(backup: Backup) -> bool:
    """A backup counts toward retention only if it finished and wrote data.

    A "succeeded" record with 0 bytes is a broken upload, not a restore point.
    """
    return backup.status == STATUS_SUCCEEDED and backup.size_bytes > 0


def backups_to_delete(backups: list[Backup], keep: int = KEEP_USABLE_BACKUPS) -> list[str]:
    """Names of backups that are safe to delete.

    - The newest `keep` usable backups are kept, however old they are.
    - Older usable backups beyond the quota are deleted.
    - Failed or empty backups cannot be restored, so they are deleted and do
      not use up the quota.
    - Running backups are never deleted: the job is still writing them.
    """
    if keep < 1:
        raise ValueError("retention must keep at least one usable backup")
    to_delete = []
    usable_kept = 0
    for backup in newest_first(backups):
        if backup.status == STATUS_RUNNING:
            continue
        if not is_usable(backup):
            to_delete.append(backup.name)
        elif usable_kept < keep:
            usable_kept += 1
        else:
            to_delete.append(backup.name)
    return to_delete


def newest_usable_backup(backups: list[Backup]) -> Backup | None:
    """The most recent backup that can be restored, or None if there is none."""
    for backup in newest_first(backups):
        if is_usable(backup):
            return backup
    return None


def backups_are_stale(backups: list[Backup], now: datetime,
                      max_age: timedelta = MAX_USABLE_BACKUP_AGE) -> bool:
    """True when no usable backup is younger than `max_age`; this should page.

    Retention only stops the damage from getting worse; someone still has to
    fix the job that has been failing every night.
    """
    newest = newest_usable_backup(backups)
    if newest is None:
        return True
    return now - newest.started_at > max_age

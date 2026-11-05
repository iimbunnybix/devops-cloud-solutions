"""Verify that last night's backup can actually be restored, not just that it ran.

The nightly job ran `dump | gzip | upload` without `pipefail`, so when the dump
failed the pipeline still exited 0 and an empty archive was uploaded every
night. The scheduler only checked the exit code, so backups "succeeded" for
weeks. This module is the restore-verification job: it restores the latest
backup into a scratch database and checks freshness, manifest checksums, that
every source table is present, and that row counts match the source within a
tolerance. Any failure becomes an alert.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

HOUR_SECONDS = 3600
# Nightly backups: allow the 24 h schedule plus 2 h of job runtime and jitter.
MAX_BACKUP_AGE_SECONDS = 26 * HOUR_SECONDS
# The source keeps changing after the dump, so allow 5% drift in row counts.
ROW_COUNT_TOLERANCE = 0.05


@dataclass(frozen=True)
class BackupJobResult:
    """What the scheduler recorded about the backup job."""
    exit_code: int


@dataclass
class Backup:
    """One backup: a dump file per table plus a manifest written by the dump step."""
    created_at: float
    files: dict[str, bytes]                    # "orders.csv" -> file contents
    manifest: dict[str, str] = field(default_factory=dict)   # file -> sha256 hex


@dataclass
class VerificationReport:
    """Result of one verification run; ok only when there are no failures."""
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def naive_backup_ok(job: BackupJobResult) -> bool:
    """The original check: trust the exit code of the backup pipeline."""
    return job.exit_code == 0


def sha256_hex(data: bytes) -> str:
    """Checksum used in the manifest."""
    return hashlib.sha256(data).hexdigest()


def table_name(file_name: str) -> str:
    """'orders.csv' -> 'orders'."""
    return file_name.rsplit(".", 1)[0]


def restore_row_counts(backup: Backup) -> dict[str, int]:
    """'Restore' each dump into a scratch database and count rows per table.

    Each non-empty line is one row; a real job would run pg_restore into a
    throwaway instance and run SELECT count(*) per table.
    """
    counts: dict[str, int] = {}
    for file_name, contents in backup.files.items():
        rows = 0
        for line in contents.decode("utf-8").splitlines():
            if line.strip():
                rows += 1
        counts[table_name(file_name)] = rows
    return counts


def check_freshness(backup: Backup, now: float, report: VerificationReport) -> None:
    """A backup that stopped being produced is as bad as a broken one."""
    age_seconds = now - backup.created_at
    if age_seconds > MAX_BACKUP_AGE_SECONDS:
        report.failures.append(f"latest backup is {age_seconds / HOUR_SECONDS:.1f}h old")


def check_manifest(backup: Backup, report: VerificationReport) -> None:
    """Catch files truncated or corrupted after the dump step wrote them."""
    if not backup.manifest:
        report.failures.append("backup has no manifest")
        return
    for file_name, expected_checksum in sorted(backup.manifest.items()):
        if file_name not in backup.files:
            report.failures.append(f"{file_name}: listed in manifest but missing")
        elif sha256_hex(backup.files[file_name]) != expected_checksum:
            report.failures.append(f"{file_name}: checksum mismatch")
    for file_name in sorted(backup.files):
        if file_name not in backup.manifest:
            report.failures.append(f"{file_name}: not listed in manifest")


def row_count_is_close(restored: int, source: int) -> bool:
    """True if the restored count is within the tolerance of the live count."""
    if source == 0:
        return restored == 0
    return abs(restored - source) / source <= ROW_COUNT_TOLERANCE


def check_row_counts(backup: Backup, source_row_counts: dict[str, int],
                     report: VerificationReport) -> None:
    """Catch empty or partial dumps: their checksums match, but data is missing."""
    restored_counts = restore_row_counts(backup)
    for table, source_count in sorted(source_row_counts.items()):
        if table not in restored_counts:
            report.failures.append(f"{table}: table missing from backup")
            continue
        restored = restored_counts[table]
        if not row_count_is_close(restored, source_count):
            report.failures.append(
                f"{table}: restored {restored} rows, source has {source_count}")


def verify_backup(backup: Backup | None, source_row_counts: dict[str, int],
                  now: float) -> VerificationReport:
    """Run every check and collect all failures (not just the first one)."""
    report = VerificationReport()
    if backup is None:
        report.failures.append("no backup found")
        return report
    check_freshness(backup, now, report)
    check_manifest(backup, report)
    check_row_counts(backup, source_row_counts, report)
    return report


def alert_message(report: VerificationReport) -> str | None:
    """One page-worthy message when verification fails, otherwise None."""
    if report.ok:
        return None
    return "backup restore verification FAILED: " + "; ".join(report.failures)

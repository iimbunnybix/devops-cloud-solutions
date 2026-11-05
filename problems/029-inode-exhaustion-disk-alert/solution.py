"""Alert on inode exhaustion, not only on bytes used.

The original bug: the disk alert on the web hosts watched bytes only
(``used / size >= 90%``). After a session cleanup cron job was lost, the app
wrote one tiny session file per visitor and never deleted them. The
filesystem ran out of inodes while ``df -h`` showed under 40% used, every
new file write failed with "No space left on device", and no alert fired.

Fix: evaluate inode usage (what ``df -i`` shows) with the same warning and
critical thresholds as bytes, and report whichever resource is worse. A
filesystem that reports zero inodes (dynamic allocation, e.g. btrfs) is
skipped for the inode check instead of crashing. A helper ranks
directories by file count, which is the first triage step during the page.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

GIB = 1024 ** 3
BLOCK_SIZE_BYTES = 4096            # smallest on-disk size of any non-empty file on ext4
BYTES_PER_INODE = 16384            # mke2fs default inode_ratio
WARNING_PERCENT = 85.0
CRITICAL_PERCENT = 95.0
NAIVE_BYTES_ALERT_PERCENT = 90.0   # the only rule the original alert had

OK, WARNING, CRITICAL = "OK", "WARNING", "CRITICAL"
SEVERITY_ORDER = {OK: 0, WARNING: 1, CRITICAL: 2}


@dataclass(frozen=True)
class FilesystemUsage:
    """One sample of a mounted filesystem, like a line of ``df`` plus ``df -i``."""

    mount: str
    bytes_total: int
    bytes_used: int
    inodes_total: int      # 0 when the filesystem allocates inodes dynamically
    inodes_used: int

    @property
    def bytes_percent(self) -> float:
        return 100.0 * self.bytes_used / self.bytes_total

    @property
    def inodes_percent(self) -> float | None:
        """None when the filesystem has no fixed inode table to run out of."""
        if self.inodes_total <= 0:
            return None
        return 100.0 * self.inodes_used / self.inodes_total

    def can_create_file(self, size_bytes: int) -> bool:
        """A new file needs a free inode AND free blocks; either one running out fails it."""
        has_inode = self.inodes_total <= 0 or self.inodes_used < self.inodes_total
        return has_inode and self.bytes_used + size_bytes <= self.bytes_total


@dataclass(frozen=True)
class DiskAlert:
    mount: str
    severity: str
    resource: str          # "bytes" or "inodes"
    percent: float


def naive_disk_alert(usage: FilesystemUsage) -> DiskAlert | None:
    """The original rule: page only when bytes used crosses 90%."""
    if usage.bytes_percent >= NAIVE_BYTES_ALERT_PERCENT:
        return DiskAlert(usage.mount, CRITICAL, "bytes", usage.bytes_percent)
    return None


def severity_for(percent: float) -> str:
    """Map a usage percentage to a severity using the shared thresholds."""
    if percent >= CRITICAL_PERCENT:
        return CRITICAL
    if percent >= WARNING_PERCENT:
        return WARNING
    return OK


def disk_alert(usage: FilesystemUsage) -> DiskAlert | None:
    """Check bytes and inodes; return the worse of the two, or None if both are fine."""
    candidates = [DiskAlert(usage.mount, severity_for(usage.bytes_percent), "bytes",
                            usage.bytes_percent)]
    inode_percent = usage.inodes_percent
    if inode_percent is not None:
        candidates.append(DiskAlert(usage.mount, severity_for(inode_percent), "inodes",
                                    inode_percent))
    worst = candidates[0]
    for candidate in candidates[1:]:
        # WHY: on a tie in severity, prefer the fuller resource, because that one fails first.
        worse_severity = SEVERITY_ORDER[candidate.severity] > SEVERITY_ORDER[worst.severity]
        same_severity = candidate.severity == worst.severity
        if worse_severity or (same_severity and candidate.percent > worst.percent):
            worst = candidate
    if worst.severity == OK:
        return None
    return worst


def ext4_volume(mount: str, size_bytes: int, base_bytes_used: int,
                base_inodes_used: int) -> FilesystemUsage:
    """A freshly formatted ext4 volume: the inode count is fixed at mkfs time."""
    return FilesystemUsage(mount, size_bytes, base_bytes_used,
                           size_bytes // BYTES_PER_INODE, base_inodes_used)


def add_small_files(usage: FilesystemUsage, count: int, file_bytes: int) -> FilesystemUsage:
    """Return the usage after writing ``count`` files, stopping when either resource runs out."""
    blocks = max(1, -(-file_bytes // BLOCK_SIZE_BYTES))   # ceil: a 1-byte file still takes a block
    on_disk = blocks * BLOCK_SIZE_BYTES
    room_by_inodes = count
    if usage.inodes_total > 0:
        room_by_inodes = usage.inodes_total - usage.inodes_used
    room_by_bytes = (usage.bytes_total - usage.bytes_used) // on_disk
    written = max(0, min(count, room_by_inodes, room_by_bytes))
    return FilesystemUsage(usage.mount, usage.bytes_total, usage.bytes_used + written * on_disk,
                           usage.inodes_total, usage.inodes_used + written)


def hourly_samples(start: FilesystemUsage, hours: int, files_per_hour: int,
                   file_bytes: int) -> list[FilesystemUsage]:
    """Usage at the end of each hour while sessions pile up with no cleanup."""
    samples = []
    usage = start
    for _ in range(hours):
        usage = add_small_files(usage, files_per_hour, file_bytes)
        samples.append(usage)
    return samples


def first_alert_hour(samples: list[FilesystemUsage], rule) -> int | None:
    """1-based hour of the first sample for which ``rule`` returns an alert."""
    for hour, usage in enumerate(samples, start=1):
        if rule(usage) is not None:
            return hour
    return None


def first_failed_write_hour(samples: list[FilesystemUsage], file_bytes: int) -> int | None:
    """1-based hour after which the app can no longer create a new session file."""
    for hour, usage in enumerate(samples, start=1):
        if not usage.can_create_file(file_bytes):
            return hour
    return None


def directories_by_file_count(paths: list[str], depth: int, top: int) -> list[tuple[str, int]]:
    """Group file paths by their first ``depth`` directories and rank by file count.

    WHY: ``du`` ranks by bytes, which points at the wrong place when the
    problem is millions of tiny files. Counting files finds the culprit.
    """
    counts: Counter[str] = Counter()
    for path in paths:
        parts = PurePosixPath(path).parent.parts[: depth + 1]   # +1 keeps the leading "/"
        counts[str(PurePosixPath(*parts))] += 1
    return counts.most_common(top)

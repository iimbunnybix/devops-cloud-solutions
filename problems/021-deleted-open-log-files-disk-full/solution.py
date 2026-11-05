"""Find the disk space that `du` cannot see: deleted files still held open.

On Linux, deleting a file only removes its name. The blocks are freed when the
last open file descriptor on it is closed. When logrotate (or a person) deletes
a log that a process still writes to, `df` keeps counting those bytes while
`du`, which walks directory entries, no longer finds them. This module scans a
/proc-style table of open file descriptors, finds deleted-but-open files on the
full mount, sums how much space would come back, and tells the operator which
process to signal (to reopen its logs) or restart, biggest win first.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Processes that reopen their log files on a signal, so no restart is needed.
# nginx reopens on USR1; rsyslogd re-opens its outputs on HUP.
DEFAULT_REOPEN_SIGNALS: dict[str, str] = {"nginx": "SIGUSR1", "rsyslogd": "SIGHUP"}


@dataclass(frozen=True)
class OpenFd:
    """One entry of /proc/<pid>/fd: what the descriptor points at."""
    fd: int
    path: str            # readlink target, without any " (deleted)" suffix
    inode: int
    size_bytes: int
    link_count: int      # st_nlink of the inode; 0 means no name is left


@dataclass
class Process:
    """A running process and its open file descriptors."""
    pid: int
    name: str
    open_fds: list[OpenFd] = field(default_factory=list)


@dataclass(frozen=True)
class LinkedFile:
    """A file that still has a name in the directory tree (what `du` sees)."""
    path: str
    inode: int
    size_bytes: int


@dataclass(frozen=True)
class Recommendation:
    """What to do with one process and how much space it frees on its own."""
    pid: int
    process_name: str
    action: str                 # e.g. "send SIGUSR1" or "restart"
    reclaimable_bytes: int      # freed once THIS process closes its fds
    deleted_paths: tuple[str, ...]


def naive_du_used_bytes(linked_files: list[LinkedFile]) -> int:
    """The original investigation: add up the files you can list (like `du`)."""
    total = 0
    for linked in linked_files:
        total += linked.size_bytes
    return total


def is_on_mount(path: str, mount_point: str) -> bool:
    """True if path lives under mount_point (so /var/logs is NOT under /var/log)."""
    if mount_point == "/":
        return path.startswith("/")
    return path == mount_point or path.startswith(mount_point.rstrip("/") + "/")


def deleted_inode_holders(processes: list[Process], mount_point: str) -> dict[int, set[int]]:
    """Map each deleted-but-open inode on the mount to the set of pids holding it.

    We use link_count == 0 instead of the " (deleted)" text in readlink output,
    because a real file can be named "report (deleted).txt".
    """
    holders: dict[int, set[int]] = {}
    for process in processes:
        for open_fd in process.open_fds:
            if open_fd.link_count != 0 or not is_on_mount(open_fd.path, mount_point):
                continue
            holders.setdefault(open_fd.inode, set()).add(process.pid)
    return holders


def inode_sizes(processes: list[Process]) -> dict[int, int]:
    """Size of every open inode (several fds may point at the same inode)."""
    sizes: dict[int, int] = {}
    for process in processes:
        for open_fd in process.open_fds:
            sizes[open_fd.inode] = open_fd.size_bytes
    return sizes


def hidden_bytes(processes: list[Process], mount_point: str) -> int:
    """Bytes `df` counts but `du` cannot find, counting each inode once."""
    sizes = inode_sizes(processes)
    total = 0
    for inode in deleted_inode_holders(processes, mount_point):
        total += sizes[inode]
    return total


def df_used_bytes(linked_files: list[LinkedFile], processes: list[Process],
                  mount_point: str) -> int:
    """What `df` reports: named files plus deleted files that are still open."""
    return naive_du_used_bytes(linked_files) + hidden_bytes(processes, mount_point)


def action_for(process_name: str, reopen_signals: dict[str, str]) -> str:
    """Prefer a log-reopen signal (no downtime); otherwise a restart is needed."""
    signal_name = reopen_signals.get(process_name)
    if signal_name is None:
        return "restart"
    return f"send {signal_name}"


def recommend(processes: list[Process], mount_point: str,
              reopen_signals: dict[str, str] | None = None) -> list[Recommendation]:
    """One recommendation per process holding deleted files, biggest win first.

    A process only gets credit for inodes nobody else holds: if a forked child
    shares the fd, closing it in the parent alone frees nothing.
    """
    signals = DEFAULT_REOPEN_SIGNALS if reopen_signals is None else reopen_signals
    holders = deleted_inode_holders(processes, mount_point)
    sizes = inode_sizes(processes)
    recommendations: list[Recommendation] = []
    for process in processes:
        exclusive_bytes = 0
        paths: list[str] = []
        seen_inodes: set[int] = set()
        for open_fd in process.open_fds:
            if open_fd.inode not in holders or open_fd.inode in seen_inodes:
                continue
            seen_inodes.add(open_fd.inode)
            paths.append(open_fd.path)
            if holders[open_fd.inode] == {process.pid}:
                exclusive_bytes += sizes[open_fd.inode]
        if paths:
            recommendations.append(Recommendation(
                process.pid, process.name, action_for(process.name, signals),
                exclusive_bytes, tuple(sorted(paths))))
    recommendations.sort(key=lambda rec: (-rec.reclaimable_bytes, rec.pid))
    return recommendations

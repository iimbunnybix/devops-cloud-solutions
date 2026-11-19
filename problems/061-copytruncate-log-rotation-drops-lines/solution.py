"""Rotate application logs without losing the lines written during rotation.

The payments API's logs were rotated nightly with logrotate's
``copytruncate``: copy app.log to app.log.1, then truncate app.log to zero.
The service keeps writing the whole time, so every line written after the
copy finished but before the truncate ran was in neither file. On a busy
night that gap swallowed the log lines an auditor later asked for.

The fix rotates by *renaming* instead. A rename is atomic and does not touch
the file's contents; the running service keeps writing to the same open file
(now called app.log.1) until it is told to reopen its log path, after which
it writes to a fresh app.log. Every line lands in exactly one file.

The file system here is a small in-memory model: a name points to an
"inode" (the file's data), and an open handle points to the inode, not the
name, which is how POSIX file systems behave.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_KEEP_GENERATIONS = 3


@dataclass
class Inode:
    """The data of one file; open handles point here, not to a name."""
    lines: list[str] = field(default_factory=list)


class FakeFileSystem:
    """Names mapped to inodes, with atomic rename like a real file system."""

    def __init__(self) -> None:
        self.names: dict[str, Inode] = {}

    def open_append(self, path: str) -> Inode:
        """Open (creating if needed) a file for appending; returns the handle."""
        if path not in self.names:
            self.names[path] = Inode()
        return self.names[path]

    def read(self, path: str) -> list[str]:
        """All lines in the file at ``path`` (empty if it does not exist)."""
        if path not in self.names:
            return []
        return list(self.names[path].lines)

    def exists(self, path: str) -> bool:
        return path in self.names

    def rename(self, old_path: str, new_path: str) -> None:
        """Point new_path at old_path's inode; open handles are unaffected."""
        self.names[new_path] = self.names.pop(old_path)

    def delete(self, path: str) -> None:
        self.names.pop(path, None)


class AppLogger:
    """The service: keeps one handle open and appends to it."""

    def __init__(self, file_system: FakeFileSystem, path: str) -> None:
        self.file_system = file_system
        self.path = path
        self.handle = file_system.open_append(path)

    def write(self, line: str) -> None:
        self.handle.lines.append(line)

    def reopen(self) -> None:
        """What the service does on SIGHUP/SIGUSR1: open its log path again."""
        self.handle = self.file_system.open_append(self.path)


def copytruncate_copy_step(file_system: FakeFileSystem, path: str) -> None:
    """Original rotation, step 1: copy the live log to path.1."""
    file_system.names[f"{path}.1"] = Inode(file_system.read(path))


def copytruncate_truncate_step(file_system: FakeFileSystem, path: str) -> None:
    """Original rotation, step 2: empty the live log in place.

    Anything written since the copy step is thrown away here.
    """
    file_system.names[path].lines.clear()


def shift_old_generations(file_system: FakeFileSystem, path: str, keep: int) -> None:
    """Make room for a new path.1: path.1 -> path.2 ..., dropping the oldest."""
    file_system.delete(f"{path}.{keep}")
    for generation in range(keep - 1, 0, -1):
        older = f"{path}.{generation}"
        if file_system.exists(older):
            file_system.rename(older, f"{path}.{generation + 1}")


def rename_rotate(file_system: FakeFileSystem, path: str,
                  keep: int = DEFAULT_KEEP_GENERATIONS) -> None:
    """Fixed rotation, step 1: rename the live log to path.1.

    The service still holds the old inode, so its writes keep landing in
    path.1 until it reopens; nothing is copied and nothing is truncated.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    shift_old_generations(file_system, path, keep)
    file_system.rename(path, f"{path}.1")


def lines_across_generations(file_system: FakeFileSystem, path: str, keep: int) -> list[str]:
    """Every stored line, oldest file first, for checking nothing was lost."""
    all_lines: list[str] = []
    for generation in range(keep, 0, -1):
        all_lines.extend(file_system.read(f"{path}.{generation}"))
    all_lines.extend(file_system.read(path))
    return all_lines

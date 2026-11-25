"""Stop a log shipper from resuming a brand-new log file at an old file's offset.

The original bug: the shipper remembered "inode -> byte offset" for each file
it tailed. Rotation deleted the old file and the app created a new one; the
filesystem handed the freed inode number to the new file, so the shipper
believed it was the same file and resumed at the old offset. Everything the
app wrote before that offset was never shipped.

Fix: the checkpoint also stores a fingerprint (a hash of the first bytes of
the file). On every poll the shipper resumes only if the inode, the
fingerprint and the size all agree; a different fingerprint, or a file that
is now shorter than the saved offset, means a new file, read from byte 0.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

FINGERPRINT_BYTES = 256      # enough to cover the first timestamped line or two
NEWLINE = b"\n"


@dataclass
class FakeFile:
    inode: int
    content: bytes = b""


@dataclass
class FakeDisk:
    """A tiny filesystem that, like ext4, reuses the lowest free inode number."""

    files: dict[str, FakeFile] = field(default_factory=dict)
    next_new_inode: int = 1000
    freed_inodes: list[int] = field(default_factory=list)

    def create(self, path: str) -> None:
        """Create an empty file, reusing a freed inode number when one exists."""
        if self.freed_inodes:
            inode = min(self.freed_inodes)
            self.freed_inodes.remove(inode)
        else:
            inode = self.next_new_inode
            self.next_new_inode += 1
        self.files[path] = FakeFile(inode)

    def append(self, path: str, data: bytes) -> None:
        self.files[path].content += data

    def delete(self, path: str) -> None:
        self.freed_inodes.append(self.files.pop(path).inode)

    def rotate_delete_and_recreate(self, path: str) -> None:
        """Rotation as the incident saw it: old file removed, a new empty one created."""
        self.delete(path)
        self.create(path)


@dataclass
class Checkpoint:
    inode: int
    offset: int
    fingerprint: str = ""          # hash of content[:fingerprint_length]
    fingerprint_length: int = 0


def fingerprint_of(content: bytes, length: int) -> str:
    """Hash of the first ``length`` bytes; two different files almost never share it."""
    return hashlib.sha256(content[:length]).hexdigest()


def complete_lines(content: bytes, offset: int) -> tuple[list[bytes], int]:
    """Whole lines after ``offset`` and the offset just past the last newline.

    WHY: a line still being written must wait for its newline, or it would be
    shipped in two broken halves.
    """
    last_newline = content.rfind(NEWLINE)
    if last_newline < offset:
        return [], offset
    end = last_newline + 1
    return content[offset:end].splitlines(), end


def naive_poll(disk: FakeDisk, path: str, checkpoints: dict[int, int]) -> list[bytes]:
    """The original tailer: trusts the inode number alone to identify a file."""
    file = disk.files[path]
    offset = checkpoints.get(file.inode, 0)
    lines, new_offset = complete_lines(file.content, offset)
    checkpoints[file.inode] = new_offset
    return lines


def is_same_file(checkpoint: Checkpoint, file: FakeFile) -> bool:
    """True only if this really is the file the checkpoint was taken on."""
    if checkpoint.inode != file.inode:
        return False
    # WHY: a file can only grow while we tail it; shorter means new or truncated.
    if len(file.content) < checkpoint.offset:
        return False
    current = fingerprint_of(file.content, checkpoint.fingerprint_length)
    return current == checkpoint.fingerprint


def updated_checkpoint(file: FakeFile, offset: int) -> Checkpoint:
    """Checkpoint over as many leading bytes as we have read, up to FINGERPRINT_BYTES."""
    # WHY: only bytes we have already read are fingerprinted, so the hash never
    # depends on data that may still be appended later.
    length = min(offset, FINGERPRINT_BYTES)
    return Checkpoint(file.inode, offset, fingerprint_of(file.content, length), length)


def poll(disk: FakeDisk, path: str, checkpoints: dict[str, Checkpoint]) -> list[bytes]:
    """Ship new complete lines, restarting at byte 0 whenever the file was replaced."""
    file = disk.files[path]
    saved = checkpoints.get(path)
    offset = 0
    if saved is not None and is_same_file(saved, file):
        offset = saved.offset
    lines, new_offset = complete_lines(file.content, offset)
    checkpoints[path] = updated_checkpoint(file, new_offset)
    return lines


def log_line(sequence: int) -> bytes:
    """One access-log line with a unique sequence number, so lost lines can be counted."""
    return f"2026-11-25T03:00:{sequence % 60:02d}Z GET /api/orders/{sequence} 200\n".encode()


def write_lines(disk: FakeDisk, path: str, first: int, count: int) -> None:
    for sequence in range(first, first + count):
        disk.append(path, log_line(sequence))

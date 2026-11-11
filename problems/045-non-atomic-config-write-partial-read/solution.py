"""Atomic config file updates for a hot-reloading service.

The deploy agent rewrote ``routes.json`` in place (open with "w", write, close).
The service polls the file and reloads it when it changes, so it sometimes read
the file while it was empty or half-written, failed to parse it and crashed the
reload loop (for line-based formats such as YAML or INI, a prefix can even parse
and the service would serve half a routing table).

The fix has two halves. The writer puts the new content in a temporary file in
the same directory, flushes and fsyncs it, then renames it over the old file
with ``os.replace``, which is atomic on POSIX, so a reader sees the old file or
the new file, never a mix. The reader validates what it loads and keeps the last
good config when a file is unreadable, so even a bad deploy cannot take it down.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

REQUIRED_KEYS = ("version", "routes")
TEMP_SUFFIX = ".tmp"


class ConfigError(Exception):
    """The config file could not be parsed or failed validation."""


class InterruptedWriter:
    """Fake writer that stops after ``stop_after_bytes`` bytes.

    It models the moment the reader happens to look at the file while a slower
    in-place write is still running (or after the writer process was killed).
    """

    def __init__(self, stop_after_bytes: int):
        self.stop_after_bytes = stop_after_bytes

    def write(self, handle, data: bytes) -> None:
        """Write only the first part of ``data``, then 'pause'."""
        handle.write(data[: self.stop_after_bytes])
        handle.flush()


class CompleteWriter:
    """Writer that writes every byte (the normal case)."""

    def write(self, handle, data: bytes) -> None:
        """Write all of ``data``."""
        handle.write(data)
        handle.flush()


def encode_config(config: dict) -> bytes:
    """Serialise a config the way the deploy agent does."""
    return json.dumps(config, indent=2, sort_keys=True).encode("utf-8")


def naive_write_config(path: Path, config: dict, writer=None) -> None:
    """Original deploy agent: truncate the live file, then write into it.

    Between the truncate and the last byte, any reader sees a partial file.
    """
    writer = writer or CompleteWriter()
    with open(path, "wb") as handle:
        writer.write(handle, encode_config(config))


def atomic_write_config(path: Path, config: dict, writer=None) -> None:
    """Write a new version next to the live file, then swap it in atomically.

    If the writer is interrupted, only the temp file is incomplete; the live
    file still holds the previous version.
    """
    writer = writer or CompleteWriter()
    # The temp file must be in the same directory: os.replace is only atomic
    # within one filesystem, and /tmp is often a different one.
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=TEMP_SUFFIX)
    data = encode_config(config)
    with os.fdopen(descriptor, "wb") as handle:
        writer.write(handle, data)
        handle.flush()
        # fsync before rename, so a power cut cannot leave a renamed but empty file.
        os.fsync(handle.fileno())
    if os.path.getsize(temp_name) != len(data):
        os.remove(temp_name)
        raise OSError("config write was interrupted; live file left unchanged")
    os.replace(temp_name, path)


def parse_config(raw: bytes) -> dict:
    """Parse and validate config bytes; raise ConfigError if unusable."""
    try:
        config = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConfigError(f"unparseable config: {error}") from error
    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")
    for key in REQUIRED_KEYS:
        if key not in config:
            raise ConfigError(f"config missing required key {key!r}")
    if not isinstance(config["routes"], dict) or not config["routes"]:
        raise ConfigError("config has no routes")
    return config


def naive_reload(path: Path) -> dict:
    """Original service reload: parse whatever is on disk (crashes on bad data)."""
    return json.loads(path.read_bytes().decode("utf-8"))


class ConfigReloader:
    """Hot reloader that keeps serving the last good config on a bad read."""

    def __init__(self, path: Path):
        self.path = path
        self.current: dict | None = None
        self.rejected_reloads = 0
        self.last_error: str | None = None

    def reload(self) -> dict:
        """Load the file if valid; otherwise keep the previous config.

        Raises ConfigError only when there has never been a good config, so a
        service cannot start on garbage but a running one never goes down.
        """
        try:
            self.current = parse_config(self.path.read_bytes())
            self.last_error = None
        except (ConfigError, FileNotFoundError) as error:
            self.rejected_reloads += 1
            self.last_error = str(error)
            if self.current is None:
                raise ConfigError(f"no valid config yet: {error}") from error
        return self.current


def sample_config(version: int, route_count: int) -> dict:
    """A routing table with ``route_count`` upstreams."""
    routes = {}
    for index in range(route_count):
        routes[f"/service-{index:03d}"] = f"http://backend-{index:03d}.internal:8080"
    return {"version": version, "routes": routes}

"""Pick the latest release by semantic-version order, not string order.

The deploy pipeline's "deploy latest" step ran `max(tags)` over release tags.
As strings, "v1.9.0" > "v1.10.0" because the character "9" sorts after "1",
so after release 1.10.0 the pipeline kept redeploying 1.9.0, and the
"roll back to previous" step picked the wrong build too. This module parses
tags into numeric parts (with SemVer pre-release rules) and only then compares.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# vMAJOR.MINOR.PATCH with an optional -prerelease part, e.g. v2.0.0-rc.1
TAG_PATTERN = re.compile(r"v([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z.-]+))?")


def naive_latest(tags: list[str]) -> str:
    """Original step: the 'largest' tag as a string (lexicographic)."""
    return max(tags)


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)


def parse_tag(tag: str) -> Version | None:
    """Turn 'v1.10.0-rc.2' into Version(1, 10, 0, ('rc', '2')); None if not a release tag."""
    # fullmatch, not match: a trailing newline or "v1.0.0junk" must not count as a release.
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch, prerelease = match.groups()
    parts = tuple(prerelease.split(".")) if prerelease else ()
    return Version(int(major), int(minor), int(patch), parts)


def _identifier_key(identifier: str) -> tuple[int, int, str]:
    """SemVer rule: numeric identifiers compare as numbers and sort before text ones."""
    if identifier.isdigit():
        return (0, int(identifier), "")
    return (1, 0, identifier)


def sort_key(version: Version) -> tuple:
    """A tuple that sorts versions in SemVer precedence order.

    A release sorts after all of its pre-releases (1.10.0-rc.1 < 1.10.0),
    so the flag is 0 for pre-releases and 1 for the final release.
    """
    release_flag = 0 if version.is_prerelease else 1
    prerelease_keys = tuple(_identifier_key(part) for part in version.prerelease)
    return (version.major, version.minor, version.patch, release_flag, prerelease_keys)


def sorted_releases(tags: list[str], include_prereleases: bool = False) -> list[str]:
    """Valid release tags oldest-to-newest; unparseable tags are skipped, never guessed at."""
    parsed: list[tuple[tuple, str]] = []
    for tag in tags:
        version = parse_tag(tag)
        if version is None:
            continue
        if version.is_prerelease and not include_prereleases:
            continue
        parsed.append((sort_key(version), tag))
    parsed.sort()
    return [tag for _key, tag in parsed]


def latest_release(tags: list[str]) -> str:
    """The newest stable release tag; raises if there is none rather than deploying junk."""
    releases = sorted_releases(tags)
    if not releases:
        raise ValueError("no valid release tags")
    return releases[-1]


def previous_release(tags: list[str], current: str) -> str:
    """The stable release just before `current`, used by the rollback step."""
    releases = sorted_releases(tags)
    if current not in releases:
        raise ValueError(f"{current} is not a known stable release")
    position = releases.index(current)
    if position == 0:
        raise ValueError(f"nothing to roll back to before {current}")
    return releases[position - 1]

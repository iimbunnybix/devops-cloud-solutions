"""Parse on/off switches from environment variables strictly.

During an incident the on-call engineer set RECOMMENDATIONS_ENABLED=false to
switch off a failing feature, but it stayed on: the service did
bool(os.environ[...]), and every non-empty string, including "false", "0" and
"off", is True in Python. This module parses switches with an explicit list of
accepted spellings and refuses to start on anything else, so a kill switch
either works or the deploy fails loudly.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Accepted spellings, compared after trimming spaces and lower-casing.
# Anything outside these two sets is a mistake we want to see at startup.
TRUE_SPELLINGS: frozenset[str] = frozenset({"1", "true", "yes", "on"})
FALSE_SPELLINGS: frozenset[str] = frozenset({"0", "false", "no", "off"})


class InvalidFlagError(ValueError):
    """Raised at startup when a switch has a value we cannot read with certainty."""


@dataclass(frozen=True)
class FlagSpec:
    """One on/off switch: its environment variable name and its default."""
    env_name: str
    default: bool


def naive_flag(environ: Mapping[str, str], spec: FlagSpec) -> bool:
    """The original code: truthiness of the raw string, so "false" means True."""
    if spec.env_name not in environ:
        return spec.default
    return bool(environ[spec.env_name])


def parse_bool(env_name: str, raw_value: str) -> bool:
    """Turn one raw value into True/False, or raise if it is not a known spelling.

    We reject an empty string too: `FLAG=` in a manifest usually means someone
    forgot the value, and guessing either way could be the dangerous choice.
    """
    normalised = raw_value.strip().lower()
    if normalised in TRUE_SPELLINGS:
        return True
    if normalised in FALSE_SPELLINGS:
        return False
    allowed = sorted(TRUE_SPELLINGS | FALSE_SPELLINGS)
    raise InvalidFlagError(
        f"{env_name}={raw_value!r} is not a boolean; use one of {allowed}")


def strict_flag(environ: Mapping[str, str], spec: FlagSpec) -> bool:
    """Read one switch: the default only when the variable is absent."""
    if spec.env_name not in environ:
        return spec.default
    return parse_bool(spec.env_name, environ[spec.env_name])


def load_flags(environ: Mapping[str, str], specs: list[FlagSpec]) -> dict[str, bool]:
    """Read every switch at startup and report ALL bad values in one error.

    Collecting every problem (instead of stopping at the first) means one failed
    deploy shows the whole list, rather than one typo per deploy attempt.
    """
    flags: dict[str, bool] = {}
    problems: list[str] = []
    for spec in specs:
        try:
            flags[spec.env_name] = strict_flag(environ, spec)
        except InvalidFlagError as error:
            problems.append(str(error))
    if problems:
        raise InvalidFlagError("; ".join(problems))
    return flags

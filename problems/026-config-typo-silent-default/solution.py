"""Fail at startup when a config variable is misspelled, instead of using a default.

The checkout service reads its settings from environment variables with
``os.environ.get(name, default)``. During a database incident, an engineer
lowered the connection limit by setting ``CHECKOUT_MAX_DB_CONNECTION=20``
(missing the final "S"). The service ignored the unknown variable, kept the
default of 200 connections per pod, and the overloaded database fell over
again on the next deploy.

The fix loads config from a declared schema: every variable with the app's
prefix must be a known setting (with a "did you mean" hint), values are
parsed and range-checked, and required settings must be present. Any problem
stops the process at startup with every error listed at once.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Mapping

APP_PREFIX = "CHECKOUT_"
SUGGESTION_CUTOFF = 0.8


@dataclass(frozen=True)
class Setting:
    name: str
    default: int | None       # None means "required, no default"
    minimum: int
    maximum: int


SETTINGS = (
    Setting("CHECKOUT_MAX_DB_CONNECTIONS", default=200, minimum=1, maximum=500),
    Setting("CHECKOUT_REQUEST_TIMEOUT_MS", default=3000, minimum=100, maximum=60_000),
    Setting("CHECKOUT_WORKERS", default=None, minimum=1, maximum=64),
)


class ConfigError(Exception):
    """One or more config problems; ``errors`` lists all of them."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("invalid configuration:\n  " + "\n  ".join(errors))
        self.errors = errors


def naive_load(environ: Mapping[str, str]) -> dict[str, int]:
    """The original loader: get(name, default), unknown variables ignored."""
    config = {}
    for setting in SETTINGS:
        default = setting.default if setting.default is not None else 1
        config[setting.name] = int(environ.get(setting.name, default))
    return config


def unknown_variable_errors(environ: Mapping[str, str]) -> list[str]:
    """Report app-prefixed variables that match no setting, with a hint."""
    known_names = [setting.name for setting in SETTINGS]
    errors = []
    for name in sorted(environ):
        # Only our own prefix: the environment also holds PATH, HOME, etc.
        if not name.startswith(APP_PREFIX) or name in known_names:
            continue
        message = f"unknown setting {name}"
        close = difflib.get_close_matches(name, known_names, n=1, cutoff=SUGGESTION_CUTOFF)
        if close:
            message += f" (did you mean {close[0]}?)"
        errors.append(message)
    return errors


def parse_setting(setting: Setting, raw: str | None) -> tuple[int | None, str | None]:
    """Return (value, error) for one setting; exactly one of them is None."""
    if raw is None:
        if setting.default is None:
            return None, f"{setting.name} is required"
        return setting.default, None
    try:
        value = int(raw.strip())
    except ValueError:
        return None, f"{setting.name}={raw!r} is not a whole number"
    if not setting.minimum <= value <= setting.maximum:
        return None, (f"{setting.name}={value} is outside "
                      f"{setting.minimum}..{setting.maximum}")
    return value, None


def load_config(environ: Mapping[str, str]) -> dict[str, int]:
    """Validated config, or ConfigError listing every problem found."""
    errors = unknown_variable_errors(environ)
    config: dict[str, int] = {}
    for setting in SETTINGS:
        value, error = parse_setting(setting, environ.get(setting.name))
        if error is not None:
            errors.append(error)
        else:
            config[setting.name] = value
    if errors:
        raise ConfigError(errors)
    return config

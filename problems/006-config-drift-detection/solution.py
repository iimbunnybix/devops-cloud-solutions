"""Detect real configuration drift between Git (desired) and the live system.

The incident: during an outage an engineer raised a service's memory limit
and opened a firewall rule by hand in the console. Neither change was put
back into Git. Six weeks later a routine deploy re-applied the Git config,
silently reverted the memory fix, and the service started OOM-crashing
again. A drift detector existed, but it compared raw JSON, reported
hundreds of differences on every run (server-added defaults, timestamps,
list order), and had been muted for months.

``detect_drift`` normalizes both sides before comparing:

* drops server-managed fields (``status``, ``metadata.uid``, timestamps, ...);
* fills documented server defaults into the desired side, so a default the
  server added is not reported as drift;
* compares set-like lists (rules, env vars) order-insensitively, keyed by a
  natural id (e.g. env var ``name``);
* canonicalizes quantities (``"1Gi"`` == ``"1024Mi"``, ``"500m"`` CPU == ``0.5``).

It returns a short list of ``Drift`` items with JSON-pointer-like paths,
classified as ``changed`` / ``added_live`` (exists only live, e.g. a manual
change) / ``missing_live``.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

# Fields the platform owns; they are never drift.
IGNORED_PATHS = {
    "status",
    "metadata.uid",
    "metadata.resourceVersion",
    "metadata.creationTimestamp",
    "metadata.generation",
    "metadata.managedFields",
    "metadata.annotations.last-applied-configuration",
}

# Defaults the server fills in when a field is omitted.
SERVER_DEFAULTS: dict[str, Any] = {
    "spec.replicas": 1,
    "spec.strategy": "RollingUpdate",
    "spec.container.imagePullPolicy": "IfNotPresent",
    "spec.container.ports[].protocol": "TCP",
    "spec.firewall[].protocol": "tcp",
}

# Lists that are really sets: compare by this key, ignore order.
SET_KEYS: dict[str, str | None] = {
    "spec.container.env": "name",
    "spec.container.ports": "containerPort",
    "spec.firewall": None,         # whole-item identity (no natural key)
}

QUANTITY_PATHS = {"spec.container.resources.memory", "spec.container.resources.cpu"}

_BIN = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
_DEC = {"k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "m": 10**-3}


def parse_quantity(v: Any) -> float:
    """Kubernetes-style quantity -> number ("1Gi" -> 1073741824, "250m" -> 0.25)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]{0,2})\s*", str(v))
    if not m:
        raise ValueError(f"not a quantity: {v!r}")
    num, unit = float(m.group(1)), m.group(2)
    if unit == "":
        return num
    if unit in _BIN:
        return num * _BIN[unit]
    if unit in _DEC:
        return num * _DEC[unit]
    raise ValueError(f"unknown unit {unit!r} in {v!r}")


@dataclass(frozen=True)
class Drift:
    path: str
    kind: str        # changed | added_live | missing_live
    desired: Any
    live: Any


def _generic(path: str) -> str:
    """'spec.firewall[2].protocol' -> 'spec.firewall[].protocol'."""
    return re.sub(r"\[[^\]]*\]", "[]", path)


def normalize(doc: Any, *, fill_defaults: bool, path: str = "") -> Any:
    """Return a canonical copy of ``doc`` suitable for comparison."""
    if path in IGNORED_PATHS:
        return None
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            p = f"{path}.{k}" if path else k
            nv = normalize(v, fill_defaults=fill_defaults, path=p)
            if nv is not None and p not in IGNORED_PATHS:
                out[k] = nv
        if fill_defaults:
            g = _generic(path)
            for dpath, dval in SERVER_DEFAULTS.items():
                parent, _, leaf = dpath.rpartition(".")
                if parent == g and leaf not in out:
                    out[leaf] = dval
        return out
    if isinstance(doc, list):
        g = _generic(path)
        items = [normalize(x, fill_defaults=fill_defaults, path=f"{path}[]") for x in doc]
        if g in SET_KEYS:
            key = SET_KEYS[g]
            if key is None:
                return {repr(_freeze(x)): x for x in items}
            return {str(x[key]): x for x in items}
        return items
    if path in QUANTITY_PATHS:
        return parse_quantity(doc)
    return doc


def _freeze(x: Any) -> Any:
    if isinstance(x, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in x.items()))
    if isinstance(x, list):
        return tuple(_freeze(v) for v in x)
    return x


def _diff(a: Any, b: Any, path: str, out: list[Drift]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = f"{path}[{k}]" if _is_set_container(path) else (f"{path}.{k}" if path else k)
            if k not in b:
                out.append(Drift(p, "missing_live", a[k], None))
            elif k not in a:
                out.append(Drift(p, "added_live", None, b[k]))
            else:
                _diff(a[k], b[k], p, out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(Drift(path, "changed", a, b))


def _is_set_container(path: str) -> bool:
    return _generic(path) in SET_KEYS


def detect_drift(desired: dict, live: dict) -> list[Drift]:
    """Meaningful differences between desired (Git) and live config."""
    d = normalize(copy.deepcopy(desired), fill_defaults=True)
    l = normalize(copy.deepcopy(live), fill_defaults=False)
    out: list[Drift] = []
    _diff(d, l, "", out)
    return out


def naive_diff(desired: Any, live: Any, path: str = "") -> list[str]:
    """The old detector: raw structural diff, order-sensitive, no ignores."""
    if isinstance(desired, dict) and isinstance(live, dict):
        out = []
        for k in sorted(set(desired) | set(live)):
            p = f"{path}.{k}" if path else k
            if k not in desired or k not in live:
                out.append(p)
            else:
                out.extend(naive_diff(desired[k], live[k], p))
        return out
    if isinstance(desired, list) and isinstance(live, list):
        if len(desired) != len(live):
            return [path]
        out = []
        for i, (x, y) in enumerate(zip(desired, live)):
            out.extend(naive_diff(x, y, f"{path}[{i}]"))
        return out
    return [] if desired == live else [path]

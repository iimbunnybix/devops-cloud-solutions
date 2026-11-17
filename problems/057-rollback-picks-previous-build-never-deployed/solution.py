"""Pick a rollback target from what actually ran in production, not from the build list.

The rollback script took "the build before the current one" from the CI
artifact list. That build had never been deployed (it failed QA), so the
emergency rollback shipped an untested release. A second rollback in a row
picked the bad release that had just been rolled back. This module derives
the target from the environment's deploy history: the newest release that
was deployed to this environment and stayed healthy, is not the one running
now, and was never itself rolled back.
"""
from __future__ import annotations

from dataclasses import dataclass

# A release must have run this long without being rolled back to count as known-good.
MIN_HEALTHY_SECONDS = 30 * 60


@dataclass(frozen=True)
class Build:
    version: str
    built_at: float


@dataclass(frozen=True)
class Deployment:
    version: str
    environment: str
    started_at: float
    succeeded: bool       # rollout finished and health checks passed
    is_rollback: bool = False


class NoRollbackTarget(RuntimeError):
    """No earlier release is known-good; a human has to choose."""


def naive_rollback_target(builds: list[Build], current_version: str) -> str:
    """Original script: the build that CI produced just before the current one."""
    ordered = sorted(builds, key=lambda build: build.built_at)
    versions = [build.version for build in ordered]
    position = versions.index(current_version)
    return versions[position - 1]


def environment_history(deployments: list[Deployment], environment: str) -> list[Deployment]:
    """This environment's successful deployments, oldest first."""
    history = []
    for deployment in deployments:
        if deployment.environment == environment and deployment.succeeded:
            history.append(deployment)
    history.sort(key=lambda deployment: deployment.started_at)
    return history


def rolled_back_versions(history: list[Deployment]) -> set[str]:
    """Versions that were replaced by a rollback: they are known to be bad."""
    bad = set()
    for index in range(1, len(history)):
        if history[index].is_rollback:
            bad.add(history[index - 1].version)
    return bad


def healthy_versions(history: list[Deployment], now: float) -> set[str]:
    """Versions that stayed in production long enough to trust them.

    A release replaced after five minutes may have been replaced because it
    was broken; one that ran for half an hour has real traffic behind it.
    """
    healthy = set()
    for index, deployment in enumerate(history):
        if index + 1 < len(history):
            ended_at = history[index + 1].started_at
        else:
            ended_at = now
        if ended_at - deployment.started_at >= MIN_HEALTHY_SECONDS:
            healthy.add(deployment.version)
    return healthy


def rollback_target(deployments: list[Deployment], environment: str, now: float) -> str:
    """Newest known-good release in this environment other than the current one."""
    history = environment_history(deployments, environment)
    if not history:
        raise NoRollbackTarget(f"{environment}: no successful deployments recorded")
    current_version = history[-1].version
    bad = rolled_back_versions(history)
    healthy = healthy_versions(history, now)
    # Walk backwards through what actually ran, newest first.
    for deployment in reversed(history):
        version = deployment.version
        if version == current_version or version in bad:
            continue
        if version in healthy:
            return version
    raise NoRollbackTarget(f"{environment}: no earlier known-good release")

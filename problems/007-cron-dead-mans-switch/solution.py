"""Dead man's switch for scheduled jobs: alert when a job does NOT run.

The original bug: the nightly database backup ran from cron on one VM and
alerted only when the backup script exited non-zero. After the VM was rebuilt
from a new base image without the cron daemon, the job simply never started.
No run means no failure, so no alert, for weeks, until someone needed a
restore.

Fix: every job reports heartbeats (``start``, ``success``, ``fail``) to a
monitor that knows its schedule. The monitor alerts on the *absence* of a
success within ``period + grace``, on runs that start but never finish within
``max_runtime``, and on explicit failures. Alerts fire once per incident and
resolve automatically when the job recovers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

OK, MISSED, NEVER_RAN, STUCK, FAILED = "OK", "MISSED", "NEVER_RAN", "STUCK", "FAILED"


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def now(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@dataclass
class JobSpec:
    name: str
    period: float          # expected seconds between successful runs
    grace: float           # tolerated lateness (queueing, slow start, DST, ...)
    max_runtime: float     # a run longer than this is considered stuck


@dataclass
class _JobState:
    spec: JobSpec
    registered_at: float
    last_success: float | None = None
    running: dict[str, float] = field(default_factory=dict)   # run_id -> start time
    last_failed: bool = False
    last_error: str = ""
    status: str = OK


@dataclass(frozen=True)
class Alert:
    job: str
    kind: str        # "FIRING" or "RESOLVED"
    status: str
    at: float
    message: str


class FailureOnlyAlerter:
    """The original setup: page only when a run reports failure."""

    def __init__(self):
        self.alerts: list[str] = []

    def on_exit(self, job: str, exit_code: int) -> None:
        if exit_code != 0:
            self.alerts.append(f"{job} failed with exit code {exit_code}")


class HeartbeatMonitor:
    def __init__(self, clock):
        self.clock = clock
        self.jobs: dict[str, _JobState] = {}

    def register(self, spec: JobSpec) -> None:
        if spec.period <= 0 or spec.grace < 0 or spec.max_runtime <= 0:
            raise ValueError("period and max_runtime must be > 0, grace >= 0")
        existing = self.jobs.get(spec.name)
        if existing is not None:
            # A config reload or monitor restart re-registers every job. Keep the
            # history (last success, running runs, current alert status) and only
            # swap the spec; resetting it would silently clear a firing alert and
            # restart the NEVER_RAN clock on every reload.
            existing.spec = spec
            return
        self.jobs[spec.name] = _JobState(spec, self.clock.now())

    def _job(self, name: str) -> _JobState:
        if name not in self.jobs:
            raise KeyError(f"unknown job {name!r}: register it first (unknown pings are a config bug)")
        return self.jobs[name]

    # --- pings sent by the job wrapper ---------------------------------------
    def start(self, name: str, run_id: str) -> None:
        self._job(name).running[run_id] = self.clock.now()

    def success(self, name: str, run_id: str) -> None:
        j = self._job(name)
        self._finish(j, run_id)
        j.last_success, j.last_failed, j.last_error = self.clock.now(), False, ""

    def fail(self, name: str, run_id: str, error: str = "") -> None:
        j = self._job(name)
        self._finish(j, run_id)
        j.last_failed, j.last_error = True, error

    def _finish(self, j: _JobState, run_id: str) -> None:
        """Forget this run, plus older runs that are already past max_runtime.

        A run that is OOM-killed or loses its host never sends a final ping.
        Once a *newer* run has finished, such an old over-time run is treated
        as dead; otherwise the job would stay STUCK forever and could never
        page MISSED again. Older runs still within max_runtime are kept, so a
        healthy overlapping run does not hide one that later hangs.
        """
        now = self.clock.now()
        started = j.running.pop(run_id, now)
        for other_id, other_started in list(j.running.items()):
            if other_started < started and now - other_started > j.spec.max_runtime:
                del j.running[other_id]

    # --- evaluation, run every minute by the monitor itself -------------------
    def status_of(self, name: str) -> str:
        j, now = self._job(name), self.clock.now()
        s = j.spec
        if any(now - started > s.max_runtime for started in j.running.values()):
            return STUCK
        if j.last_failed:
            return FAILED
        reference = j.last_success if j.last_success is not None else j.registered_at
        if now > reference + s.period + s.grace:
            return MISSED if j.last_success is not None else NEVER_RAN
        return OK

    def evaluate(self) -> list[Alert]:
        """Return state *transitions* only, so on-call gets one page per incident."""
        out = []
        now = self.clock.now()
        for name, j in self.jobs.items():
            new = self.status_of(name)
            if new == j.status:
                continue
            if new == OK:
                out.append(Alert(name, "RESOLVED", OK, now, f"{name} is healthy again"))
            else:
                out.append(Alert(name, "FIRING", new, now, self._describe(j, new)))
            j.status = new
        return out

    def _describe(self, j: _JobState, status: str) -> str:
        now, s = self.clock.now(), j.spec
        if status == STUCK:
            return f"{s.name}: a run has been going for over {s.max_runtime:.0f}s"
        if status == FAILED:
            return f"{s.name}: last run failed: {j.last_error or 'no error message'}"
        if status == NEVER_RAN:
            return f"{s.name}: no successful run since it was registered {now - j.registered_at:.0f}s ago"
        return f"{s.name}: last success {now - j.last_success:.0f}s ago (expected every {s.period:.0f}s)"

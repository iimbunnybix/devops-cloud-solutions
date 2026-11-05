"""Stop a scheduled job from running twice at the same time.

The invoice export runs every 5 minutes. Usually it takes 2 minutes, but when
the database is slow one run can take 15+ minutes. The scheduler does not
care and starts the next copy on time, so two or three copies run at once.
Each copy picks up every invoice that is not yet marked exported, so the
same invoices are sent to the accounting system twice.

The fix wraps each run in a lease-based lock. A run must acquire the lease
before it starts; if another run holds it, the new start is skipped and
counted. The lease has an expiry, so a crashed run cannot block the job
forever, and an owner token, so a slow run whose lease already expired
cannot release (or renew) the lease that a newer run now holds.
"""
from __future__ import annotations

import itertools
from operator import attrgetter
from dataclasses import dataclass, field

INTERVAL_SECONDS = 300            # the scheduler fires every 5 minutes
LEASE_TTL_SECONDS = 1800          # longer than any healthy run, short enough to recover
INVOICES_PER_INTERVAL = 10        # new invoices waiting at each tick
CRASHED = None                    # duration marker: the run dies without cleaning up


class FakeClock:
    """A clock the tests move by hand, so lease expiry is deterministic."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now


@dataclass
class Lease:
    owner_token: str
    expires_at: float


class LeaseLock:
    """A named lock that expires on its own and only its owner can release."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.leases: dict[str, Lease] = {}
        self._token_counter = itertools.count(1)

    def acquire(self, name: str, ttl: float) -> str | None:
        """Return a fresh owner token, or None if a live lease is held by someone."""
        current = self.leases.get(name)
        # WHY: an expired lease belongs to a run that crashed or hung; take it over.
        if current is not None and current.expires_at > self.clock.now:
            return None
        token = f"run-{next(self._token_counter)}"
        self.leases[name] = Lease(token, self.clock.now + ttl)
        return token

    def renew(self, name: str, token: str, ttl: float) -> bool:
        """Extend our own live lease (a heartbeat for long runs)."""
        if not self.holds(name, token):
            return False
        self.leases[name].expires_at = self.clock.now + ttl
        return True

    def release(self, name: str, token: str) -> bool:
        """Release only if we still own it; never delete another run's lease."""
        current = self.leases.get(name)
        if current is None or current.owner_token != token:
            return False
        del self.leases[name]
        return True

    def holds(self, name: str, token: str) -> bool:
        """True if ``token`` owns a lease on ``name`` that has not expired."""
        current = self.leases.get(name)
        return (current is not None and current.owner_token == token
                and current.expires_at > self.clock.now)


@dataclass
class Run:
    started_at: float
    ends_at: float
    invoices: set[int]               # snapshot taken at start
    lock_token: str | None = None


@dataclass
class ExportReport:
    export_counts: dict[int, int] = field(default_factory=dict)
    max_concurrent_runs: int = 0
    skipped_starts: int = 0

    @property
    def duplicate_exports(self) -> int:
        """How many times an invoice was sent again after its first export."""
        return sum(count - 1 for count in self.export_counts.values() if count > 1)


class InvoiceExportJob:
    """Simulates the scheduler firing the export every INTERVAL_SECONDS."""

    JOB_NAME = "invoice-export"

    def __init__(self, lock: LeaseLock | None) -> None:
        # lock=None is the original setup: no guard against overlap.
        self.lock = lock
        self.clock = lock.clock if lock else FakeClock()
        self.pending: set[int] = set()
        self.running: list[Run] = []
        self.report = ExportReport()
        self._next_invoice = itertools.count(1)

    def run_schedule(self, durations: list[float | None]) -> ExportReport:
        """Fire one tick per entry; durations[i] is how long tick i's run would take."""
        for tick, duration in enumerate(durations):
            tick_time = tick * INTERVAL_SECONDS
            self._finish_runs_up_to(tick_time)
            self.clock.now = tick_time
            self._add_new_invoices()
            self._start_run(duration)
        self._finish_runs_up_to(float("inf"))
        return self.report

    def _add_new_invoices(self) -> None:
        for _ in range(INVOICES_PER_INTERVAL):
            self.pending.add(next(self._next_invoice))

    def _start_run(self, duration: float | None) -> None:
        """Start a run unless the lock says another one is still going."""
        token = None
        if self.lock is not None:
            token = self.lock.acquire(self.JOB_NAME, LEASE_TTL_SECONDS)
            if token is None:
                self.report.skipped_starts += 1
                return
        # WHY: a crashed process exports nothing and never releases its lease;
        # only the lease expiry can free the job again.
        if duration is CRASHED:
            return
        # WHY: like the real job, a run exports whatever is unexported *when it
        # starts*; invoices are only marked exported when the run finishes.
        ends_at = self.clock.now + duration
        self.running.append(Run(self.clock.now, ends_at, set(self.pending), token))
        self.report.max_concurrent_runs = max(self.report.max_concurrent_runs,
                                              len(self.running))

    def _finish_runs_up_to(self, until: float) -> None:
        """Complete every run that ends at or before ``until``, in end order."""
        finishing = [run for run in self.running if run.ends_at <= until]
        finishing.sort(key=attrgetter("ends_at"))
        for run in finishing:
            self.clock.now = run.ends_at
            for invoice in run.invoices:
                counts = self.report.export_counts
                counts[invoice] = counts.get(invoice, 0) + 1
            self.pending -= run.invoices
            if self.lock is not None:
                self.lock.release(self.JOB_NAME, run.lock_token)
            self.running.remove(run)


def naive_export(durations: list[float | None]) -> ExportReport:
    """The original scheduler: start every tick, no lock."""
    return InvoiceExportJob(lock=None).run_schedule(durations)


def guarded_export(durations: list[float | None]) -> ExportReport:
    """The fix: every run must hold the lease, overlapping starts are skipped."""
    return InvoiceExportJob(lock=LeaseLock(FakeClock())).run_schedule(durations)

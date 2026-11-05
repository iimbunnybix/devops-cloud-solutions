"""Rotate a database password without failing a single login.

The original rotation changed the password of the one database user the
checkout service uses, then rolled out the new secret pod by pod. Every pod
that had not restarted yet still had the old password cached, so each new
connection it opened failed authentication until its turn in the rollout.

The fix uses two database users ("slots") and a small state machine:
1. create the new password on the *inactive* slot (the old one keeps working);
2. publish a secret with ``current`` = new and ``previous`` = old, and roll
   out; pods try ``current`` first and fall back to ``previous``;
3. retire the old slot only after a safety check proves no pod still uses
   the old secret and the database shows no open connections for it.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Callable

SLOT_USERS = ("checkout_a", "checkout_b")    # two users, used in turn
NEW_CONNECTIONS_PER_STEP = 5                 # pool churn per pod per rollout step
STABLE, ROLLING_OUT = "STABLE", "ROLLING_OUT"


def random_password() -> str:
    """Generated at runtime; never a literal in source control."""
    return secrets.token_urlsafe(24)


@dataclass(frozen=True)
class Credential:
    username: str
    password: str


@dataclass(frozen=True)
class SecretVersion:
    version: int
    current: Credential
    previous: Credential | None = None


class FakeDatabase:
    """Just enough of a database: logins and open connections per user."""

    def __init__(self) -> None:
        self.passwords: dict[str, str] = {}
        self.open_connections: dict[str, int] = {}
        self.failed_logins = 0

    def set_password(self, username: str, password: str) -> None:
        self.passwords[username] = password

    def revoke(self, username: str) -> None:
        # WHY: like ALTER ROLE ... NOLOGIN, existing sessions are not killed.
        self.passwords.pop(username, None)

    def connect(self, credential: Credential) -> bool:
        """Open a connection; an unknown user or wrong password is an auth error."""
        if self.passwords.get(credential.username) != credential.password:
            self.failed_logins += 1
            return False
        user = credential.username
        self.open_connections[user] = self.open_connections.get(user, 0) + 1
        return True

    def close(self, username: str) -> None:
        self.open_connections[username] -= 1


class SecretStore:
    """Holds the latest secret version; pods read it only when they start."""

    def __init__(self, first: SecretVersion) -> None:
        self.latest = first

    def publish(self, current: Credential, previous: Credential | None) -> SecretVersion:
        self.latest = SecretVersion(self.latest.version + 1, current, previous)
        return self.latest


class Pod:
    """An app instance with a cached secret and a pool of open connections."""

    def __init__(self, name: str, secret: SecretVersion) -> None:
        self.name = name
        self.secret = secret
        self.connections: list[str] = []      # username of each open connection

    def open_connection(self, db: FakeDatabase, try_previous: bool = True) -> bool:
        """Try ``current`` and, if rejected, ``previous`` (unless disabled)."""
        candidates = [self.secret.current]
        if try_previous and self.secret.previous is not None:
            candidates.append(self.secret.previous)
        for credential in candidates:
            if db.connect(credential):
                self.connections.append(credential.username)
                return True
        return False

    def restart(self, db: FakeDatabase, store: SecretStore) -> None:
        """Close the pool and load the newest secret, like a rolling deploy."""
        for username in self.connections:
            db.close(username)
        self.connections = []
        self.secret = store.latest


def rolling_restart(pods: list[Pod], db: FakeDatabase, store: SecretStore) -> int:
    """Restart pods one at a time; return the logins that failed meanwhile.

    After each restart every pod opens a few new connections, as real pools
    do when connections hit their max lifetime or traffic grows.
    """
    failures_before = db.failed_logins
    for pod_to_restart in pods:
        pod_to_restart.restart(db, store)
        for pod in pods:
            for _ in range(NEW_CONNECTIONS_PER_STEP):
                pod.open_connection(db)
    return db.failed_logins - failures_before


def naive_rotate(db: FakeDatabase, store: SecretStore) -> None:
    """The original rotation: change the only user's password in place."""
    username = store.latest.current.username
    new_credential = Credential(username, random_password())
    # WHY this breaks: from this line on, pods with the cached old password
    # fail every new login until the rollout restarts them.
    db.set_password(username, new_credential.password)
    store.publish(new_credential, previous=None)


class RotationBlocked(Exception):
    """Retiring the old credential would break a consumer that still uses it."""


class DualCredentialRotation:
    """STABLE -> start() -> ROLLING_OUT -> finish() -> STABLE (slots swapped)."""

    def __init__(self, db: FakeDatabase, store: SecretStore,
                 make_password: Callable[[], str] = random_password) -> None:
        self.db = db
        self.store = store
        self.make_password = make_password
        self.phase = STABLE

    def start(self) -> SecretVersion:
        """Put a new password on the inactive slot and publish current+previous."""
        if self.phase != STABLE:
            raise RotationBlocked("a rotation is already in progress")
        old = self.store.latest.current
        new_username = self._other_slot(old.username)
        new = Credential(new_username, self.make_password())
        # WHY: the database must accept the new credential before any pod sees it.
        self.db.set_password(new_username, new.password)
        self.phase = ROLLING_OUT
        return self.store.publish(current=new, previous=old)

    def finish(self, pods: list[Pod]) -> None:
        """Revoke the old slot, but only if nothing is still using it."""
        if self.phase != ROLLING_OUT:
            raise RotationBlocked("no rotation in progress")
        problems = self.blockers(pods)
        if problems:
            raise RotationBlocked("; ".join(problems))
        rotating = self.store.latest
        self.db.revoke(rotating.previous.username)
        self.store.publish(current=rotating.current, previous=None)
        self.phase = STABLE

    def blockers(self, pods: list[Pod]) -> list[str]:
        """Reasons the old credential is still in use (empty means safe)."""
        rotating = self.store.latest
        old_username = rotating.previous.username
        problems = []
        for pod in pods:
            if pod.secret.version < rotating.version:
                problems.append(f"{pod.name} still runs secret v{pod.secret.version}")
        # WHY: pods are not the only consumers; the database sees everyone,
        # including a forgotten cron job or a pooled connection.
        open_count = self.db.open_connections.get(old_username, 0)
        if open_count > 0:
            problems.append(f"{open_count} open connection(s) as {old_username}")
        return problems

    @staticmethod
    def _other_slot(username: str) -> str:
        first, second = SLOT_USERS
        return second if username == first else first

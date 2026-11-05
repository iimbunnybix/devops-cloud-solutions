import itertools
import unittest

from solution import (NEW_CONNECTIONS_PER_STEP, ROLLING_OUT, STABLE, Credential,
                      DualCredentialRotation, FakeDatabase, Pod, RotationBlocked,
                      SecretStore, SecretVersion, naive_rotate, rolling_restart)

POD_COUNT = 4
INITIAL_POOL = 3


def fake_password_factory():
    """Deterministic, obviously fake passwords built at runtime."""
    counter = itertools.count(1)

    def make_password():
        return "test-pw-" + str(next(counter))

    return make_password


def build_system():
    """One DB user in use, a secret store at v1, and 4 pods with open pools."""
    db = FakeDatabase()
    first = Credential("checkout_a", "test-pw-initial")
    db.set_password(first.username, first.password)
    store = SecretStore(SecretVersion(1, first))
    pods = [Pod(f"checkout-{index}", store.latest) for index in range(POD_COUNT)]
    for pod in pods:
        for _ in range(INITIAL_POOL):
            pod.open_connection(db)
    return db, store, pods


def new_rotation(db, store):
    return DualCredentialRotation(db, store, make_password=fake_password_factory())


class ReproduceProblem(unittest.TestCase):
    def test_in_place_password_change_fails_logins_during_rollout(self):
        db, store, pods = build_system()
        naive_rotate(db, store)
        failed = rolling_restart(pods, db, store)
        # Pods not yet restarted fail every new login: 3+2+1+0 pods x 5.
        self.assertEqual(failed, (3 + 2 + 1) * NEW_CONNECTIONS_PER_STEP)

    def test_pod_with_cached_old_password_cannot_connect(self):
        db, store, pods = build_system()
        naive_rotate(db, store)
        self.assertFalse(pods[0].open_connection(db))


class FixDualCredentialRotation(unittest.TestCase):
    def setUp(self):
        self.db, self.store, self.pods = build_system()
        self.rotation = new_rotation(self.db, self.store)

    def test_rollout_has_no_failed_logins(self):
        self.rotation.start()
        self.assertEqual(rolling_restart(self.pods, self.db, self.store), 0)

    def test_new_credential_uses_the_other_slot(self):
        version = self.rotation.start()
        self.assertEqual(version.current.username, "checkout_b")
        self.assertEqual(version.previous.username, "checkout_a")

    def test_finish_after_full_rollout_revokes_old_slot(self):
        self.rotation.start()
        rolling_restart(self.pods, self.db, self.store)
        self.rotation.finish(self.pods)
        self.assertEqual(self.rotation.phase, STABLE)
        self.assertNotIn("checkout_a", self.db.passwords)
        self.assertIsNone(self.store.latest.previous)

    def test_second_rotation_returns_to_first_slot(self):
        for _ in range(2):
            self.rotation.start()
            rolling_restart(self.pods, self.db, self.store)
            self.rotation.finish(self.pods)
        self.assertEqual(self.store.latest.current.username, "checkout_a")
        self.assertEqual(self.db.failed_logins, 0)

    def test_cannot_start_twice(self):
        self.rotation.start()
        with self.assertRaises(RotationBlocked):
            self.rotation.start()

    def test_cannot_finish_without_start(self):
        with self.assertRaises(RotationBlocked):
            self.rotation.finish(self.pods)


class FixSafetyCheck(unittest.TestCase):
    def setUp(self):
        self.db, self.store, self.pods = build_system()
        self.rotation = new_rotation(self.db, self.store)
        self.rotation.start()

    def test_refuses_to_revoke_while_pods_run_old_secret(self):
        self.pods[0].restart(self.db, self.store)
        with self.assertRaises(RotationBlocked) as raised:
            self.rotation.finish(self.pods)
        self.assertIn("checkout-1 still runs secret v1", str(raised.exception))
        self.assertIn("checkout_a", self.db.passwords)
        self.assertEqual(self.rotation.phase, ROLLING_OUT)

    def test_refuses_while_unknown_consumer_holds_old_connections(self):
        rolling_restart(self.pods, self.db, self.store)
        # A nightly report job nobody remembered, still on the old password.
        forgotten_job = Pod("nightly-report", SecretVersion(1, Credential("checkout_a", "test-pw-initial")))
        forgotten_job.open_connection(self.db)
        with self.assertRaises(RotationBlocked) as raised:
            self.rotation.finish(self.pods)
        self.assertIn("1 open connection(s) as checkout_a", str(raised.exception))

    def test_blockers_empty_once_everything_moved(self):
        rolling_restart(self.pods, self.db, self.store)
        self.assertEqual(self.rotation.blockers(self.pods), [])


class FixFallbackToPrevious(unittest.TestCase):
    def test_pod_falls_back_to_previous_when_current_is_rejected(self):
        db, store, _ = build_system()
        old = store.latest.current
        # The new user has not reached this database yet (e.g. replica lag).
        secret = SecretVersion(2, Credential("checkout_b", "test-pw-not-yet-there"), old)
        pod = Pod("checkout-9", secret)
        self.assertTrue(pod.open_connection(db))
        self.assertEqual(pod.connections, ["checkout_a"])

    def test_without_fallback_the_same_pod_fails(self):
        db, store, _ = build_system()
        old = store.latest.current
        secret = SecretVersion(2, Credential("checkout_b", "test-pw-not-yet-there"), old)
        self.assertFalse(Pod("checkout-9", secret).open_connection(db, try_previous=False))


if __name__ == "__main__":
    unittest.main()

import unittest

from solution import (CHECKSUM_ANNOTATION, Orchestrator, Pod, UpstreamPaymentsApi,
                      config_checksum, drift_report, failed_calls, naive_render_deployment,
                      render_deployment, stale_pods)

IMAGE = "registry.example.internal/checkout:1.42.0"
CONFIG = "checkout-config"
REPLICAS = 6
KEY_NAME = "PAYMENTS_API_KEY"


def fake_api_key(version: int) -> str:
    """Obviously fake key, built at runtime."""
    return "-".join(["fake", "payments", "key", f"v{version}"])


def config_with_key(version: int) -> dict[str, str]:
    return {KEY_NAME: fake_api_key(version), "TIMEOUT_MS": "800"}


class Environment:
    """Config store, orchestrator and upstream API, deployed with key v1."""

    def __init__(self) -> None:
        self.store = {CONFIG: config_with_key(1)}
        self.orchestrator = Orchestrator(self.store)
        self.upstream = UpstreamPaymentsApi()
        self.upstream.valid_keys.add(fake_api_key(1))

    def rotate_key_and_revoke_old(self) -> None:
        """The rotation runbook: write v2 to the store, enable it, revoke v1."""
        self.store[CONFIG] = config_with_key(2)
        self.upstream.valid_keys.add(fake_api_key(2))
        self.upstream.valid_keys.discard(fake_api_key(1))


class ReproduceProblem(unittest.TestCase):
    def setUp(self) -> None:
        self.env = Environment()
        self.env.orchestrator.apply(naive_render_deployment(IMAGE, CONFIG, REPLICAS))

    def test_reapplying_unchanged_spec_after_rotation_does_not_roll_out(self):
        self.env.rotate_key_and_revoke_old()
        rolled_out = self.env.orchestrator.apply(naive_render_deployment(IMAGE, CONFIG, REPLICAS))
        self.assertFalse(rolled_out)
        self.assertEqual(self.env.orchestrator.rollouts, 1)

    def test_every_pod_fails_once_the_old_key_is_revoked(self):
        self.env.rotate_key_and_revoke_old()
        self.env.orchestrator.apply(naive_render_deployment(IMAGE, CONFIG, REPLICAS))
        self.assertEqual(failed_calls(self.env.orchestrator.pods, self.env.upstream, KEY_NAME), REPLICAS)


class FixChecksumAnnotation(unittest.TestCase):
    def setUp(self) -> None:
        self.env = Environment()
        self.env.orchestrator.apply(render_deployment(IMAGE, CONFIG, REPLICAS, self.env.store))

    def test_config_change_produces_a_different_spec(self):
        before = render_deployment(IMAGE, CONFIG, REPLICAS, self.env.store)
        self.env.rotate_key_and_revoke_old()
        after = render_deployment(IMAGE, CONFIG, REPLICAS, self.env.store)
        self.assertNotEqual(before, after)

    def test_rotation_triggers_a_rollout(self):
        self.env.rotate_key_and_revoke_old()
        rolled_out = self.env.orchestrator.apply(render_deployment(IMAGE, CONFIG, REPLICAS, self.env.store))
        self.assertTrue(rolled_out)
        self.assertEqual(self.env.orchestrator.rollouts, 2)

    def test_no_pod_fails_after_rotation(self):
        self.env.rotate_key_and_revoke_old()
        self.env.orchestrator.apply(render_deployment(IMAGE, CONFIG, REPLICAS, self.env.store))
        self.assertEqual(failed_calls(self.env.orchestrator.pods, self.env.upstream, KEY_NAME), 0)

    def test_unchanged_config_does_not_restart_pods(self):
        rolled_out = self.env.orchestrator.apply(render_deployment(IMAGE, CONFIG, REPLICAS, self.env.store))
        self.assertFalse(rolled_out)

    def test_reordering_keys_is_not_a_change(self):
        reordered = {"TIMEOUT_MS": "800", KEY_NAME: fake_api_key(1)}
        self.assertEqual(config_checksum(reordered), config_checksum(config_with_key(1)))

    def test_annotation_does_not_contain_the_secret(self):
        spec = render_deployment(IMAGE, CONFIG, REPLICAS, self.env.store)
        annotations = dict(spec.annotations)
        self.assertIn(CHECKSUM_ANNOTATION, annotations)
        self.assertNotIn(fake_api_key(1), str(spec))


class FixDriftCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.desired = config_with_key(2)
        self.old = config_with_key(1)

    def pod(self, name: str, values: dict[str, str]) -> Pod:
        return Pod(name, values, config_checksum(values))

    def test_pod_on_old_config_is_reported(self):
        pods = [self.pod("web-0", self.desired), self.pod("web-1", self.old)]
        self.assertEqual(stale_pods(pods, config_checksum(self.desired)), ["web-1"])

    def test_no_stale_pods_when_all_match(self):
        pods = [self.pod("web-0", self.desired), self.pod("web-1", self.desired)]
        self.assertEqual(stale_pods(pods, config_checksum(self.desired)), [])

    def test_naive_deploy_shows_up_as_drift_after_rotation(self):
        env = Environment()
        env.orchestrator.apply(naive_render_deployment(IMAGE, CONFIG, REPLICAS))
        env.rotate_key_and_revoke_old()
        report = drift_report(env.orchestrator, CONFIG)
        desired = config_checksum(env.store[CONFIG])
        self.assertEqual(report[desired], [])                       # nobody runs the new config
        self.assertEqual(len(report[config_checksum(self.old)]), REPLICAS)


if __name__ == "__main__":
    unittest.main()

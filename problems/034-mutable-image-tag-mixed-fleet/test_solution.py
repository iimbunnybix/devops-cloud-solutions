import unittest

from solution import (DIGEST_PREFIX, FakeNode, FakeRegistry, UnknownImage, create_pinned_release,
                      find_drifted_nodes, naive_release, rollout, split_reference)

REPO = "registry.internal:5000/shop/api"
SERVICE = "api"
NODE_COUNT = 6
HALF = NODE_COUNT // 2


def make_nodes() -> list[FakeNode]:
    return [FakeNode(f"node-{i}") for i in range(NODE_COUNT)]


def distinct_digests(nodes: list[FakeNode]) -> set[str]:
    return {node.running_digest for node in nodes}


def rollout_with_tag_moved_halfway(make_release):
    """Start a rollout of :v2, let CI re-push :v2 halfway, finish the rollout."""
    registry = FakeRegistry()
    first_build = registry.push(REPO, "v2", "build 101")
    release = make_release(registry)
    nodes = make_nodes()
    rollout(release, SERVICE, nodes[:HALF], registry)
    registry.push(REPO, "v2", "build 102 (hotfix re-tagged as v2)")
    rollout(release, SERVICE, nodes[HALF:], registry)
    return registry, release, nodes, first_build


def naive_v2(registry):
    return naive_release("2.0", {SERVICE: f"{REPO}:v2"})


def pinned_v2(registry):
    return create_pinned_release("2.0", {SERVICE: f"{REPO}:v2"}, registry)


def deploy_good_then_bad_latest(make_release):
    """Release 1 and release 2 both say :latest; return (registry, nodes, release1, good digest)."""
    registry = FakeRegistry()
    good = registry.push(REPO, "latest", "build 200 (good)")
    release_1 = make_release("1", registry)
    nodes = make_nodes()
    rollout(release_1, SERVICE, nodes, registry)
    registry.push(REPO, "latest", "build 201 (bad)")
    release_2 = make_release("2", registry)
    rollout(release_2, SERVICE, nodes, registry)
    return registry, nodes, release_1, good


def naive_latest(version, registry):
    return naive_release(version, {SERVICE: f"{REPO}:latest"})


def pinned_latest(version, registry):
    return create_pinned_release(version, {SERVICE: f"{REPO}:latest"}, registry)


class ReproduceProblem(unittest.TestCase):
    def test_tag_moved_mid_rollout_leaves_a_mixed_fleet(self):
        _, _, nodes, _ = rollout_with_tag_moved_halfway(naive_v2)
        self.assertEqual(len(distinct_digests(nodes)), 2)

    def test_rollback_to_the_same_tag_redeploys_the_bad_build(self):
        registry, nodes, release_1, good = deploy_good_then_bad_latest(naive_latest)
        rollout(release_1, SERVICE, nodes, registry)          # "roll back"
        self.assertNotIn(good, distinct_digests(nodes))


class FixPinnedDigests(unittest.TestCase):
    def test_release_stores_a_digest_not_a_tag(self):
        registry = FakeRegistry()
        digest = registry.push(REPO, "v2", "build 101")
        release = pinned_v2(registry)
        self.assertEqual(release.images[SERVICE], f"{REPO}@{digest}")

    def test_tag_moved_mid_rollout_does_not_split_the_fleet(self):
        _, _, nodes, first_build = rollout_with_tag_moved_halfway(pinned_v2)
        self.assertEqual(distinct_digests(nodes), {first_build})

    def test_rollback_restores_the_exact_previous_build(self):
        registry, nodes, release_1, good = deploy_good_then_bad_latest(pinned_latest)
        rollout(release_1, SERVICE, nodes, registry)
        self.assertEqual(distinct_digests(nodes), {good})

    def test_fleet_check_passes_when_every_node_runs_the_release(self):
        registry, release, nodes, _ = rollout_with_tag_moved_halfway(pinned_v2)
        self.assertEqual(find_drifted_nodes(release, SERVICE, nodes, registry), [])

    def test_fleet_check_reports_nodes_on_a_different_digest(self):
        registry, release, nodes, first_build = rollout_with_tag_moved_halfway(pinned_v2)
        nodes[4].deploy(f"{REPO}:v2", registry)               # someone pulled the tag by hand
        drift = find_drifted_nodes(release, SERVICE, nodes, registry)
        self.assertEqual([d.node for d in drift], ["node-4"])
        self.assertEqual(drift[0].expected_digest, first_build)

    def test_fleet_check_reports_nodes_that_never_deployed(self):
        registry = FakeRegistry()
        registry.push(REPO, "v2", "build 101")
        release = pinned_v2(registry)
        nodes = make_nodes()
        rollout(release, SERVICE, nodes[:HALF], registry)
        drift = find_drifted_nodes(release, SERVICE, nodes, registry)
        self.assertEqual([d.node for d in drift], ["node-3", "node-4", "node-5"])

    def test_unknown_tag_fails_at_release_time(self):
        with self.assertRaises(UnknownImage):
            create_pinned_release("3.0", {SERVICE: f"{REPO}:v3"}, FakeRegistry())

    def test_already_pinned_reference_is_kept(self):
        registry = FakeRegistry()
        digest = registry.push(REPO, "v2", "build 101")
        release = create_pinned_release("2.0", {SERVICE: f"{REPO}@{digest}"}, registry)
        self.assertEqual(release.images[SERVICE], f"{REPO}@{digest}")

    def test_digest_missing_from_registry_is_rejected(self):
        with self.assertRaises(UnknownImage):
            FakeRegistry().resolve(f"{REPO}@{DIGEST_PREFIX}{'0' * 64}")


class ParseReferences(unittest.TestCase):
    def test_registry_port_is_not_mistaken_for_a_tag(self):
        self.assertEqual(split_reference("registry.internal:5000/shop/api"),
                         ("registry.internal:5000/shop/api", "latest", ""))

    def test_tag_after_registry_port(self):
        self.assertEqual(split_reference("registry.internal:5000/shop/api:v2"),
                         ("registry.internal:5000/shop/api", "v2", ""))

    def test_digest_reference(self):
        self.assertEqual(split_reference("shop/api@sha256:abc"), ("shop/api", "", "sha256:abc"))

    def test_tag_next_to_digest_is_ignored(self):
        self.assertEqual(split_reference("shop/api:v2@sha256:abc"), ("shop/api", "", "sha256:abc"))


if __name__ == "__main__":
    unittest.main()

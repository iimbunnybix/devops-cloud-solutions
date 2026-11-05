import copy
import unittest

from solution import detect_drift, naive_diff, parse_quantity

DESIRED = {
    "metadata": {"name": "checkout", "labels": {"team": "payments"}},
    "spec": {
        "replicas": 6,
        "container": {
            "image": "registry.local/checkout:1.42.0",
            "resources": {"memory": "1Gi", "cpu": "500m"},
            "env": [{"name": "LOG_LEVEL", "value": "info"},
                    {"name": "DB_POOL", "value": "10"}],
            "ports": [{"containerPort": 8080}],
        },
        "firewall": [{"port": 443, "cidr": "0.0.0.0/0"}],
    },
}


def live_from(desired):
    """What the platform returns for an untouched apply of `desired`."""
    live = copy.deepcopy(desired)
    live["metadata"].update(uid="7f1c", resourceVersion="88123",
                            creationTimestamp="2026-09-01T10:00:00Z", generation=14)
    live["status"] = {"readyReplicas": 6, "conditions": [{"type": "Available"}]}
    c = live["spec"]["container"]
    c["imagePullPolicy"] = "IfNotPresent"
    c["resources"] = {"memory": "1024Mi", "cpu": 0.5}
    c["env"] = list(reversed(c["env"]))
    c["ports"] = [{"containerPort": 8080, "protocol": "TCP"}]
    live["spec"]["strategy"] = "RollingUpdate"
    live["spec"]["firewall"] = [{"port": 443, "cidr": "0.0.0.0/0", "protocol": "tcp"}]
    return live


class OldDetectorWasNoise(unittest.TestCase):
    def test_naive_diff_flags_untouched_resource(self):
        noise = naive_diff(DESIRED, live_from(DESIRED))
        self.assertGreaterEqual(len(noise), 10)          # every run "drifted"
        self.assertEqual(detect_drift(DESIRED, live_from(DESIRED)), [])


class RealDrift(unittest.TestCase):
    def setUp(self):
        self.live = live_from(DESIRED)

    def test_manual_memory_hotfix_is_reported(self):
        self.live["spec"]["container"]["resources"]["memory"] = "2Gi"
        [d] = detect_drift(DESIRED, self.live)
        self.assertEqual((d.path, d.kind), ("spec.container.resources.memory", "changed"))
        self.assertEqual((d.desired, d.live), (2**30, 2 * 2**30))

    def test_manually_opened_firewall_rule_is_added_live(self):
        self.live["spec"]["firewall"].append({"port": 22, "cidr": "0.0.0.0/0", "protocol": "tcp"})
        [d] = detect_drift(DESIRED, self.live)
        self.assertEqual(d.kind, "added_live")
        self.assertTrue(d.path.startswith("spec.firewall["))
        self.assertEqual(d.live["port"], 22)

    def test_removed_env_var_is_missing_live(self):
        self.live["spec"]["container"]["env"] = [{"name": "LOG_LEVEL", "value": "info"}]
        [d] = detect_drift(DESIRED, self.live)
        self.assertEqual((d.path, d.kind), ("spec.container.env[DB_POOL]", "missing_live"))

    def test_changed_env_value_is_keyed_by_name(self):
        for e in self.live["spec"]["container"]["env"]:
            if e["name"] == "LOG_LEVEL":
                e["value"] = "debug"
        [d] = detect_drift(DESIRED, self.live)
        self.assertEqual(d.path, "spec.container.env[LOG_LEVEL].value")

    def test_scaled_replicas_and_non_default_value(self):
        self.live["spec"]["replicas"] = 2
        self.live["spec"]["container"]["imagePullPolicy"] = "Always"
        paths = {d.path for d in detect_drift(DESIRED, self.live)}
        self.assertEqual(paths, {"spec.replicas", "spec.container.imagePullPolicy"})

    def test_omitted_replicas_uses_server_default(self):
        desired = copy.deepcopy(DESIRED)
        del desired["spec"]["replicas"]
        live = live_from(desired)
        live["spec"]["replicas"] = 1
        self.assertEqual(detect_drift(desired, live), [])
        live["spec"]["replicas"] = 3
        self.assertEqual([d.path for d in detect_drift(desired, live)], ["spec.replicas"])

    def test_inputs_are_not_mutated(self):
        before = copy.deepcopy(DESIRED)
        detect_drift(DESIRED, self.live)
        self.assertEqual(DESIRED, before)


class PortsKeyedByPortAndProtocol(unittest.TestCase):
    """Issue #9: 53/TCP and 53/UDP must be two separate ports."""

    def doc(self, ports):
        return {"spec": {"container": {"image": "dns:1", "ports": ports}}}

    def test_deleting_tcp_half_of_dual_protocol_port_is_missing_live(self):
        desired = self.doc([{"containerPort": 53, "protocol": "TCP"},
                            {"containerPort": 53, "protocol": "UDP"}])
        live = self.doc([{"containerPort": 53, "protocol": "UDP"}])
        drifts = [d for d in detect_drift(desired, live) if "ports" in d.path]
        self.assertEqual([(d.path, d.kind) for d in drifts],
                         [("spec.container.ports[53/TCP]", "missing_live")])

    def test_deleting_udp_half_is_missing_live_not_protocol_change(self):
        desired = self.doc([{"containerPort": 53, "protocol": "TCP"},
                            {"containerPort": 53, "protocol": "UDP"}])
        live = self.doc([{"containerPort": 53, "protocol": "TCP"}])
        drifts = [d for d in detect_drift(desired, live) if "ports" in d.path]
        self.assertEqual([(d.path, d.kind) for d in drifts],
                         [("spec.container.ports[53/UDP]", "missing_live")])

    def test_desired_port_without_protocol_matches_live_tcp(self):
        live = live_from(DESIRED)
        self.assertNotIn("protocol", DESIRED["spec"]["container"]["ports"][0])
        self.assertEqual(detect_drift(DESIRED, live), [])

    def test_duplicate_port_entries_raise_instead_of_overwriting(self):
        desired = self.doc([{"containerPort": 53, "protocol": "UDP"},
                            {"containerPort": 53, "protocol": "UDP"}])
        with self.assertRaises(ValueError):
            detect_drift(desired, desired)


class Quantities(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_quantity("1Gi"), parse_quantity("1024Mi"))
        self.assertEqual(parse_quantity("500m"), 0.5)
        self.assertEqual(parse_quantity("2"), 2)
        self.assertEqual(parse_quantity(1.5), 1.5)
        self.assertEqual(parse_quantity("1G"), 1e9)

    def test_invalid(self):
        for bad in ("lots", "1Xi", ""):
            with self.assertRaises(ValueError):
                parse_quantity(bad)


if __name__ == "__main__":
    unittest.main()

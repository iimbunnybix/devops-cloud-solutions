import unittest

from solution import (ERROR, WARNING, Certificate, ChainError, assemble_bundle,
                      build_path, deploy_gate, lint_served_chain,
                      naive_deploy_check, strict_client_verifies)

ROOT_NAME = "Example Root CA X1"
ROOT = Certificate(ROOT_NAME, ROOT_NAME, is_ca=True)
INTERMEDIATE = Certificate("Example Issuing CA R3", ROOT_NAME, is_ca=True)
LEAF = Certificate("api.example.com", "Example Issuing CA R3")
TRUSTED_ROOTS = {ROOT_NAME}

# Two-level chain: leaf -> issuing CA -> policy CA -> root.
POLICY_CA = Certificate("Example Policy CA P1", ROOT_NAME, is_ca=True)
ISSUING_UNDER_POLICY = Certificate("Example Issuing CA E5", "Example Policy CA P1", is_ca=True)
DEEP_LEAF = Certificate("pay.example.com", "Example Issuing CA E5")
UNRELATED_CA = Certificate("Other Issuing CA", ROOT_NAME, is_ca=True)


def severities(findings) -> list[str]:
    return [finding.severity for finding in findings]


class ReproduceProblem(unittest.TestCase):
    def test_leaf_only_bundle_passes_the_browser_check(self):
        self.assertTrue(naive_deploy_check([LEAF], TRUSTED_ROOTS, browser_cached=[INTERMEDIATE]))

    def test_leaf_only_bundle_fails_for_strict_clients(self):
        self.assertFalse(strict_client_verifies([LEAF], TRUSTED_ROOTS))


class FixVerification(unittest.TestCase):
    def test_full_chain_verifies_for_strict_clients(self):
        self.assertTrue(strict_client_verifies([LEAF, INTERMEDIATE], TRUSTED_ROOTS))

    def test_two_intermediates_verify_in_order(self):
        served = [DEEP_LEAF, ISSUING_UNDER_POLICY, POLICY_CA]
        self.assertTrue(strict_client_verifies(served, TRUSTED_ROOTS))

    def test_missing_issuer_is_named_in_the_error(self):
        with self.assertRaisesRegex(ChainError, "Example Issuing CA R3"):
            build_path(LEAF, [], TRUSTED_ROOTS)

    def test_non_ca_certificate_cannot_act_as_issuer(self):
        fake_issuer = Certificate("Example Issuing CA R3", ROOT_NAME, is_ca=False)
        self.assertFalse(strict_client_verifies([LEAF, fake_issuer], TRUSTED_ROOTS))

    def test_untrusted_root_is_rejected_even_if_served(self):
        self.assertFalse(strict_client_verifies([LEAF, INTERMEDIATE, ROOT], set()))

    def test_certificate_loop_is_rejected(self):
        ca_a = Certificate("CA A", "CA B", is_ca=True)
        ca_b = Certificate("CA B", "CA A", is_ca=True)
        leaf = Certificate("loop.example.com", "CA A")
        with self.assertRaisesRegex(ChainError, "loop"):
            build_path(leaf, [ca_a, ca_b], TRUSTED_ROOTS)

    def test_empty_bundle_does_not_verify(self):
        self.assertFalse(strict_client_verifies([], TRUSTED_ROOTS))


class FixLint(unittest.TestCase):
    def test_leaf_only_bundle_is_an_error(self):
        findings = lint_served_chain([LEAF], TRUSTED_ROOTS)
        self.assertEqual(severities(findings), [ERROR])
        self.assertIn("Example Issuing CA R3", findings[0].message)

    def test_correct_bundle_has_no_findings(self):
        self.assertEqual(lint_served_chain([LEAF, INTERMEDIATE], TRUSTED_ROOTS), [])

    def test_intermediate_first_is_an_error(self):
        findings = lint_served_chain([INTERMEDIATE, LEAF], TRUSTED_ROOTS)
        self.assertIn(ERROR, severities(findings))

    def test_out_of_order_intermediates_are_a_warning(self):
        served = [DEEP_LEAF, POLICY_CA, ISSUING_UNDER_POLICY]
        self.assertEqual(severities(lint_served_chain(served, TRUSTED_ROOTS)), [WARNING, WARNING])

    def test_included_root_is_only_a_warning(self):
        findings = lint_served_chain([LEAF, INTERMEDIATE, ROOT], TRUSTED_ROOTS)
        self.assertEqual(severities(findings), [WARNING])

    def test_empty_bundle_is_an_error(self):
        self.assertEqual(severities(lint_served_chain([], TRUSTED_ROOTS)), [ERROR])


class FixDeployGate(unittest.TestCase):
    def test_gate_blocks_leaf_only_bundle(self):
        self.assertFalse(deploy_gate([LEAF], TRUSTED_ROOTS))

    def test_gate_allows_bundle_with_harmless_warning(self):
        self.assertTrue(deploy_gate([LEAF, INTERMEDIATE, ROOT], TRUSTED_ROOTS))

    def test_assembled_bundle_passes_the_gate(self):
        pool = [UNRELATED_CA, POLICY_CA, ISSUING_UNDER_POLICY, INTERMEDIATE, ROOT]
        bundle = assemble_bundle(DEEP_LEAF, pool, TRUSTED_ROOTS)
        self.assertEqual(bundle, [DEEP_LEAF, ISSUING_UNDER_POLICY, POLICY_CA])
        self.assertTrue(deploy_gate(bundle, TRUSTED_ROOTS))

    def test_assembling_without_the_intermediate_fails_loudly(self):
        with self.assertRaises(ChainError):
            assemble_bundle(LEAF, [UNRELATED_CA], TRUSTED_ROOTS)


if __name__ == "__main__":
    unittest.main()

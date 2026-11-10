"""Catch a TLS bundle that is missing its intermediate certificate before deploy.

After a certificate renewal, the API servers were given only the leaf
certificate instead of the full chain. Desktop browsers kept working because
they already had the intermediate cached (or fetch it themselves), so the
post-deploy check in a browser passed. Mobile apps and partner integrations
only use what the server sends, could not reach a trusted root, and failed
with "unable to get local issuer certificate". This module verifies a bundle
the way a strict client does, lints its order and contents, and assembles the
correct leaf-plus-intermediates bundle from a pool of CA certificates.
"""
from __future__ import annotations

from dataclasses import dataclass

MAX_CHAIN_DEPTH = 8   # real chains are 2-4 long; stops loops in bad bundles
ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Certificate:
    """The parts of an X.509 certificate that matter for building a chain."""
    subject: str
    issuer: str
    is_ca: bool = False

    @property
    def self_signed(self) -> bool:
        return self.subject == self.issuer


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str


class ChainError(Exception):
    """No path from the leaf to a trusted root could be built."""


def find_issuer(cert: Certificate, candidates: list[Certificate]) -> Certificate | None:
    """Return the CA certificate in ``candidates`` that issued ``cert``."""
    for candidate in candidates:
        if candidate.is_ca and candidate.subject == cert.issuer:
            return candidate
    return None


def build_path(leaf: Certificate, available: list[Certificate],
               trusted_roots: set[str]) -> list[Certificate]:
    """Walk from the leaf to a trusted root using only ``available`` certs.

    Returns the leaf and the intermediates, in order. Raises ChainError naming
    the first issuer that could not be found.
    """
    path = [leaf]
    current = leaf
    for _ in range(MAX_CHAIN_DEPTH):
        # Clients trust a chain once a certificate is signed by a root they
        # already have; the root itself does not need to be sent.
        if current.issuer in trusted_roots:
            return path
        issuer = find_issuer(current, available)
        if issuer is None:
            raise ChainError(f"missing issuer certificate {current.issuer!r}")
        if issuer in path:
            raise ChainError(f"certificate loop at {issuer.subject!r}")
        path.append(issuer)
        current = issuer
    raise ChainError("chain is longer than the maximum depth")


def verifies(served: list[Certificate], trusted_roots: set[str],
             extra_known: list[Certificate] | None = None) -> bool:
    """Would a client with these roots (and cached intermediates) accept it?"""
    if not served:
        return False
    available = list(served[1:])
    if extra_known:
        available.extend(extra_known)
    try:
        build_path(served[0], available, trusted_roots)
    except ChainError:
        return False
    return True


def naive_deploy_check(served: list[Certificate], trusted_roots: set[str],
                       browser_cached: list[Certificate]) -> bool:
    """Original check: open the site in a browser that has intermediates cached."""
    return verifies(served, trusted_roots, extra_known=browser_cached)


def strict_client_verifies(served: list[Certificate], trusted_roots: set[str]) -> bool:
    """A mobile app or server-side HTTP client: only what the server sends."""
    return verifies(served, trusted_roots)


def lint_order(served: list[Certificate]) -> list[Finding]:
    """Each certificate should be followed by the one that issued it."""
    findings = []
    if served[0].is_ca:
        findings.append(Finding(ERROR, f"first certificate {served[0].subject!r} is a CA, not the leaf"))
    for position in range(len(served) - 1):
        current, following = served[position], served[position + 1]
        if current.issuer != following.subject:
            findings.append(Finding(WARNING, f"{following.subject!r} does not issue {current.subject!r}; "
                                             "some clients reject out-of-order chains"))
    return findings


def lint_served_chain(served: list[Certificate], trusted_roots: set[str]) -> list[Finding]:
    """Everything a pre-deploy gate should say about a bundle."""
    if not served:
        return [Finding(ERROR, "bundle is empty")]
    findings = lint_order(served)
    try:
        build_path(served[0], served[1:], trusted_roots)
    except ChainError as error:
        findings.append(Finding(ERROR, f"strict clients will fail: {error}"))
    for cert in served[1:]:
        # Sending the root is harmless but wastes bytes on every handshake.
        if cert.self_signed:
            findings.append(Finding(WARNING, f"bundle includes root {cert.subject!r}; clients ignore it"))
    return findings


def deploy_gate(served: list[Certificate], trusted_roots: set[str]) -> bool:
    """Block the deploy if any finding is an error."""
    for finding in lint_served_chain(served, trusted_roots):
        if finding.severity == ERROR:
            return False
    return True


def assemble_bundle(leaf: Certificate, ca_pool: list[Certificate],
                    trusted_roots: set[str]) -> list[Certificate]:
    """Build the bundle to deploy: the leaf, then each intermediate in order."""
    return build_path(leaf, ca_pool, trusted_roots)

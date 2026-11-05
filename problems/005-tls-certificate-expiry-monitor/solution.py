"""Catch TLS certificates that are about to expire *before* clients break.

The incident: an auto-renewal job had been failing quietly for weeks. Nobody
noticed until the certificate on the payments API expired and every mobile
client failed the TLS handshake. The old check only looked at one IP per
hostname and only at the leaf certificate.

``CertMonitor`` checks every backend address of every hostname and every
certificate in the served chain, and reports:

* ``EXPIRED`` / ``CRITICAL`` / ``WARNING`` by days left on the earliest-
  expiring cert in the chain (an expired intermediate breaks clients too);
* ``RENEWAL_OVERDUE`` when a cert is past the point where automation should
  already have renewed it - the early signal that renewal is broken;
* ``INCONSISTENT`` when backends behind one hostname serve different certs
  (a renewed cert that never reached some load-balancer nodes);
* ``UNREACHABLE`` instead of silently skipping endpoints that fail to answer;
* ``UNPARSEABLE`` when a served cert has a missing or malformed ``notAfter``.

Fetching is injected (``fetch(host, address) -> list[dict]`` in the
``ssl.SSLSocket.getpeercert()`` format) so the logic is testable offline.
"""
from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from typing import Callable, Iterable

DAY = 86_400

Fetch = Callable[[str, str], list[dict]]


@dataclass(frozen=True)
class Finding:
    host: str
    severity: str        # "page" | "ticket"
    kind: str            # EXPIRED, CRITICAL, WARNING, RENEWAL_OVERDUE, INCONSISTENT, UNREACHABLE, UNPARSEABLE
    detail: str
    days_left: float | None = None


def not_after_epoch(cert: dict) -> float:
    """Parse an OpenSSL ``notAfter`` string, e.g. 'Jan  5 09:34:43 2027 GMT'."""
    return float(ssl.cert_time_to_seconds(cert["notAfter"]))


def _cn(cert: dict) -> str:
    for rdn in cert.get("subject", ()):
        for k, v in rdn:
            if k == "commonName":
                return v
    return "?"


class CertMonitor:
    """Evaluate certificate health for a set of hostnames.

    ``endpoints`` maps hostname -> list of backend addresses (all A records /
    LB nodes). Thresholds are in days. ``renew_before_days`` is when the
    renewal automation is configured to renew (Let's Encrypt clients
    commonly renew at 30 days left).
    """

    def __init__(self, fetch: Fetch, *, warn_days: float = 21, page_days: float = 7,
                 renew_before_days: float = 30, clock: Callable[[], float] = time.time):
        if not page_days < warn_days:
            raise ValueError("page_days must be smaller than warn_days")
        self.fetch = fetch
        self.warn_days, self.page_days = warn_days, page_days
        self.renew_before_days = renew_before_days
        self.clock = clock

    def check(self, endpoints: dict[str, Iterable[str]]) -> list[Finding]:
        findings: list[Finding] = []
        now = self.clock()
        for host, addresses in sorted(endpoints.items()):
            served: dict[str, tuple] = {}       # address -> chain fingerprint
            worst: tuple[float, str] | None = None
            for addr in addresses:
                try:
                    chain = self.fetch(host, addr)
                    if not chain:
                        raise ValueError("empty certificate chain")
                except Exception as e:     # noqa: BLE001 - any failure is a finding
                    findings.append(Finding(host, "page", "UNREACHABLE",
                                            f"{addr}: {type(e).__name__}: {e}"))
                    continue
                # Parse every expiry date before using any of them. A cert with a
                # missing or garbled notAfter must become a finding for this
                # address, not an exception that aborts the scan of every host.
                try:
                    expiries = [not_after_epoch(cert) for cert in chain]
                except (KeyError, ValueError, TypeError) as e:
                    findings.append(Finding(host, "page", "UNPARSEABLE",
                                            f"{addr}: bad notAfter: {type(e).__name__}: {e}"))
                    continue
                served[addr] = tuple((c.get("serialNumber"), c["notAfter"]) for c in chain)
                for pos, cert in enumerate(chain):
                    left = (expiries[pos] - now) / DAY
                    role = "leaf" if pos == 0 else f"chain[{pos}]"
                    label = f"{addr} {role} CN={_cn(cert)}"
                    if worst is None or left < worst[0]:
                        worst = (left, label)
            if worst is not None:
                findings.extend(self._expiry_findings(host, *worst))
            if len(set(served.values())) > 1:
                groups: dict[tuple, list[str]] = {}
                for addr, fp in served.items():
                    groups.setdefault(fp, []).append(addr)
                desc = "; ".join(f"{sorted(a)} serial={fp[0][0]}" for fp, a in groups.items())
                findings.append(Finding(host, "ticket", "INCONSISTENT",
                                        f"backends serve different certs: {desc}"))
        return findings

    def _expiry_findings(self, host: str, left: float, label: str) -> list[Finding]:
        out = []
        if left <= 0:
            out.append(Finding(host, "page", "EXPIRED", f"{label} expired {-left:.1f}d ago", left))
        elif left <= self.page_days:
            out.append(Finding(host, "page", "CRITICAL", f"{label} expires in {left:.1f}d", left))
        elif left <= self.warn_days:
            out.append(Finding(host, "ticket", "WARNING", f"{label} expires in {left:.1f}d", left))
        # Renewal should have happened at renew_before_days; allow 2 days of retries.
        if 0 < left <= self.renew_before_days - 2:
            out.append(Finding(host, "ticket", "RENEWAL_OVERDUE",
                               f"{label} not renewed; automation renews at "
                               f"{self.renew_before_days:g}d", left))
        return out


def format_not_after(epoch: float) -> str:
    """Inverse of ``not_after_epoch`` (handy for building test fixtures)."""
    t = time.gmtime(epoch)
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[t.tm_mon - 1]} {t.tm_mday:2d} {t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d} {t.tm_year} GMT"

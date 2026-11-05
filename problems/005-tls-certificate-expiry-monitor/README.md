# 005 · Every mobile client failed to connect to the payments API at 09:34 because its TLS certificate had expired

## Problem
At 09:34 UTC the certificate on `payments-api` expired. Every iOS and Android
client failed the TLS handshake, card payments stopped for 52 minutes, and the
status page showed green throughout, because the health checks ran inside the
cluster over plain HTTP. The certificate was supposed to auto-renew. The
renewal job had been failing on a DNS-01 permissions error for three weeks,
and its failures went to a log nobody watched.

## Root cause
Several gaps added up:

- **Alerting only at the end.** The only check fired at 7 days left on the
  leaf certificate, and it checked one IP per hostname. The load balancer had
  three nodes; the probed node had been manually updated during an earlier
  test, so the check stayed green while the other two served the old cert.
- **No signal that renewal had stopped.** Automation renews at 30 days left, so
  a cert below ~28 days means renewal is broken, weeks before expiry. Nobody
  watched for that.
- **Only the leaf was checked.** An expiring intermediate in the served
  chain breaks clients just as hard.
- **Probe errors were skipped**, so an unreachable node looked like "no findings".

## Solution
`CertMonitor.check()` takes every hostname with **all** its backend addresses
and evaluates the full served chain:

| Finding | When | Severity |
|---|---|---|
| `EXPIRED` | earliest cert in chain is past `notAfter` | page |
| `CRITICAL` | ≤ 7 days left | page |
| `WARNING` | ≤ 21 days left | ticket |
| `RENEWAL_OVERDUE` | ≤ `renew_before_days − 2` (28 by default) | ticket |
| `INCONSISTENT` | backends of one hostname serve different certs | ticket |
| `UNREACHABLE` | probe failed or returned no chain | page |
| `UNPARSEABLE` | a served cert has a missing or malformed `notAfter` | page |

`RENEWAL_OVERDUE` is the key alert. It fires about three weeks before expiry,
which is enough time to fix the automation calmly. The 2-day grace period
avoids noise while the renewal job is still retrying. The test
`test_leaf_only_single_ip_check_reports_healthy` reproduces the incident:
the old-style check sees 60 days on node `.1`, while the monitor reports that
node `.2` is 3 days from expiry and serving a different certificate.

Dates are parsed with `ssl.cert_time_to_seconds` (OpenSSL's `notAfter`
format), and the clock and fetcher are injected so tests run offline.

## Trade-offs
- Fetching is left to the caller. `ssl.SSLSocket.getpeercert()` returns only
  the leaf; for the full chain use `SSLSocket.get_verified_chain()` /
  `get_unverified_chain()` (Python 3.13+) or `openssl s_client -showcerts`.
  Probe each backend IP with SNI set to the hostname.
- Probing from inside the network does not prove what *clients* see (CDN,
  external LB). Also run an external synthetic check.
- This is a point-in-time scanner, not an inventory. Certificates you don't
  list are not checked. Feed endpoints from DNS/LB configuration or
  Certificate Transparency logs for your domains so new hosts are covered.
- Managed certificates (ACM, Google-managed, cert-manager) renew themselves
  but can still fail (e.g. failed DNS validation). Alert on their renewal
  status as well as on days left.

## Run
```bash
python -m unittest -v test_solution.py
```

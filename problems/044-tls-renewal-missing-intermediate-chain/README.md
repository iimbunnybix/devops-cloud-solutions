# 044 · After a certificate renewal the mobile app and partner integrations failed with "unable to get local issuer certificate", while the website worked in every browser

## Problem
The public API `api.example.com` sits behind a fleet of NGINX servers. Its
certificate was renewed by a script that copied the new files into place. The
script used the file with only the leaf certificate (certbot's `cert.pem`)
instead of the one with the leaf plus the intermediate (`fullchain.pem`).

The post-deploy check was an engineer opening the site in Chrome: green
padlock, done. Within an hour:

- the Android and iOS apps showed "can't connect" for every API call,
- partner back-ends (Java, Go and Python clients) failed with
  `unable to get local issuer certificate` and stopped sending orders,
- uptime checks written with `curl` went red, but the on-call engineer
  checked in a browser, saw it working and suspected the monitors.

It took two hours of support tickets before someone ran
`openssl s_client -showcerts` and saw a single certificate in the chain.

## Root cause
A client trusts a certificate only if it can build a path from it to a root
in its trust store. Roots sign intermediates, and intermediates sign leaf
certificates, so the server must send the leaf **and** the intermediates.

Browsers often hide a missing intermediate: they cache intermediates seen on
other sites, ship preloaded lists, or download the issuer from the URL in the
certificate (AIA fetching). Most non-browser clients (Go's and Java's default
TLS stacks, OpenSSL-based tools such as curl and Python `requests`, and
many mobile HTTP stacks) use only what the server sends. The check
used the one kind of client that could not see the bug.

## Solution
`solution.py` checks the bundle the way a strict client would, before and
after deploy:

1. **`build_path`** walks from the leaf to a trusted root using only the
   certificates in the bundle. Only CA certificates may act as issuers, and
   loops or over-long chains are rejected. The error names the missing issuer.
2. **`strict_client_verifies`** is what a mobile app or server-side client
   sees. In the tests, the leaf-only bundle passes the old browser-style
   check (intermediate cached) but fails this one.
3. **`lint_served_chain`** reports errors (empty bundle, leaf not first, no
   path to a root) and warnings (out-of-order certificates, a root included
   in the bundle: harmless but sent on every handshake).
4. **`deploy_gate`** blocks the rollout on any error.
5. **`assemble_bundle`** builds the bundle from the leaf and a pool of CA
   certificates, so the deploy script never chooses the file by hand.

## Trade-offs
- This models chain building by subject and issuer name only. Real clients
  also check signatures, key identifiers, validity dates, name constraints
  and key usage. In production, run the gate with a real TLS library
  (`ssl` with an empty intermediate cache, or `openssl verify -untrusted`)
  against the actual PEM files, and probe the live endpoint after deploy.
- Some CAs offer cross-signed chains for old clients. A bundle that is valid
  for new clients can still fail on old devices whose trust store lacks the
  newer root; test against the oldest client trust store you support.
- Probing the endpoint with a strict client (as the uptime checks did) also
  catches this. The fix there is cultural: trust the strict monitor over a
  browser.

## Run
```bash
python -m unittest -v test_solution.py
```

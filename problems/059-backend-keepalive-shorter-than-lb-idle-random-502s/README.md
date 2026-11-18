# 059 · Clients saw a handful of random 502s every hour from a healthy service, never reproducible and never in the app logs

## Problem
An internal pricing API runs on Node.js behind a managed load balancer. It
gets a request every few seconds, not a heavy load. A few times an hour a
caller got `502 Bad Gateway`. The backend's own logs had no matching request
and no error, CPU and memory were flat, and retrying always worked. The
payments team still had to handle the failures, and on-call spent days on
it because nothing could be reproduced on purpose.

In the simulation in the tests (10,000 requests with random gaps of 0-8 s,
50 ms each), the original settings give 4 random 502s and open 3,799 new
TCP connections.

## Root cause
Both sides of a pooled HTTP keep-alive connection can close it when idle:

- the load balancer reuses idle backend connections for up to **60 s**
  (the AWS ALB default idle timeout);
- the backend closes idle connections after **5 s** (the Node.js
  `server.keepAliveTimeout` default).

When the backend closes a connection, the balancer only learns about it once
the FIN packet arrives. If the balancer sends the next request in that small
window, the request lands on a socket that is already closed, gets a TCP
reset and the balancer returns 502. The request never reaches the
application, so it never appears in its logs. Low, irregular traffic makes it
worse: gaps of about 5 s are common, so the race happens regularly.

## Solution
Make the balancer always the side that closes idle connections: set the
backend's keep-alive timeout **longer** than the balancer's idle timeout,
with a margin (`safe_backend_keepalive_ms`: 60 s + 5 s = 65 s). The balancer
then closes any connection it no longer wants itself, which is never a race,
and never reuses one the backend has given up on.

`LoadBalancerPool` simulates the balancer's connection pool with a FIN delay
to show the race; with the fixed setting the same traffic gives 0 502s and
opens only 63 connections. `keepalive_problems` is a config check to run in
CI against the balancer and service settings, so the two timeouts cannot
drift apart again.

## Trade-offs
- Longer keep-alive keeps more idle sockets open on the backend (memory and
  file descriptors). With many balancer nodes this can be thousands; size
  `ulimit -n` accordingly.
- In Node.js `headersTimeout` must also be larger than `keepAliveTimeout`,
  or a similar race reappears on the next request's headers.
- Alternatives: turn off upstream keep-alive (every request pays a new TCP
  and maybe TLS handshake), lower the balancer's idle timeout below the
  backend's, or retry idempotent requests on connection reset at the proxy
  (Envoy and nginx can; managed balancers often cannot).
- The same rule applies to every hop: client -> CDN -> balancer -> sidecar ->
  app. Each hop's upstream must keep connections open longer than its
  downstream.

## Run
```bash
python -m unittest -v test_solution.py
```

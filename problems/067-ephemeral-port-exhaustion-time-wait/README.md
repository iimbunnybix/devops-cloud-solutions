# 067 · At peak traffic the pricing service failed 1 in 5 calls with "Cannot assign requested address" while the upstream was idle

## Problem
The checkout service calls the pricing service through one internal load
balancer address. Each checkout pod sends about 600 pricing calls per second
at the evening peak, 300 during the day. Every evening, a few minutes into
the peak, a fifth of pricing calls failed instantly with `connect():
Cannot assign requested address` (`EADDRNOTAVAIL`). Checkout fell back to
cached prices, which were wrong during a promotion.

The pricing service and the load balancer showed low CPU and no errors:
the failing calls never left the checkout pods. Adding pricing replicas
did nothing. Restarting checkout pods "fixed" it for about a minute.

## Root cause
The HTTP client opened a **new TCP connection for every request** and closed
it afterwards. The side that closes a TCP connection first keeps it in
**TIME_WAIT** for 60 seconds on Linux, and while it is there, its local
port cannot be reused for a connection to the same destination address and
port. The default local port range (`net.ipv4.ip_local_port_range = 32768
60999`) has **28,232** ports.

So towards one destination, the most new connections a pod can open per
second is about 28,232 / 60 = **470**. In the simulation (`naive_run`, 600
requests per second for 120 seconds):

- ports run out after **47 seconds**, with all 28,232 ports in TIME_WAIT;
- **15,536** of 72,000 requests (21.6%) fail; ports come back in a burst
  after 60 s, then run out again, which is why restarts helped briefly;
- at 300 requests per second nothing fails, which is why load tests at
  "normal" traffic passed.

## Solution
**Reuse connections** (`KeepAlivePool`, `pooled_run`). With HTTP keep-alive,
a connection serves request after request, so the number of local ports in
use depends on **concurrency**, not on request rate. By Little's law, 600
requests per second at 20 ms each need `600 × 0.02 =` **12** connections.
The pool opens those once and keeps them, with an upper bound
(`max_connections`, 20 here); load above what the pool can carry fails fast
instead of opening more sockets. The same peak then runs with **0 failures**,
12 ports in use and none in TIME_WAIT.

`max_sustainable_rate` gives the ceiling for clients that cannot pool. It
is a good alert: page when the new-connection rate to one destination goes
over about half of it, or when the TIME_WAIT socket count (`ss -tan state
time-wait | wc -l`) grows towards the port range.

## Trade-offs
- Long-lived connections stick to the upstream instances they were opened
  to; new pricing replicas get no traffic until connections are recycled.
  Close pooled connections after a maximum lifetime (a few minutes) or a
  number of requests.
- Keep-alive needs the client's idle timeout to be shorter than the load
  balancer's, or reused connections hit resets (see problem 059 for the server side of the same mismatch).
- Widening `ip_local_port_range` or enabling `net.ipv4.tcp_tw_reuse` raises
  the ceiling for outbound connections but does not remove the per-request
  handshake cost; treat them as stopgaps. Do not shorten TIME_WAIT itself:
  it protects new connections from stray packets of old ones.
- The limit is per destination address and port. Spreading calls over
  several load balancer addresses also raises it, but only hides the
  missing connection reuse.

## Run
```bash
python -m unittest -v test_solution.py
```

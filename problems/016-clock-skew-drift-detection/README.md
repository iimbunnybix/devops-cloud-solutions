# 016 · Users on one region were logged out with "token issued in the future" while logs showed replies arriving before requests

## Problem
A VM running the session service was restored from a snapshot and the
`chronyd` time-sync daemon did not start again. Its clock ran slow by about
200 ppm, which is **17.28 seconds per day**. Ten days later it was three
minutes behind the auth servers.

Then two things broke at once:

- Fresh login tokens carry an `iat` (issued-at) from the auth server. The
  session service, three minutes behind, saw them as "issued in the future"
  and rejected them, because the usual leeway is 60 seconds. Every user
  routed to that VM was logged out in a loop.
- Its log lines were three minutes older than everyone else's, so in the
  log search the response appeared before the request and traces fell apart.

A clock-skew alert existed but had been muted months before as "too noisy".

## Root cause
The skew check computed **server receive time − client send time**. That is
the real offset *plus* the one-way network delay. On a perfectly synced host
whose network path occasionally slows to 300–800 ms, the simulated check
crosses the 0.5 s threshold on **65 of 1,000 samples**. The alert paged on
network congestion, so it was muted, and real drift had no alarm.

## Solution
Use the NTP method. One exchange with a trusted time server records four
timestamps: client send `t0`, server receive `t1`, server send `t2`, client
receive `t3`.

- **Offset** = `((t1 − t0) + (t2 − t3)) / 2`. With equal delays in both
  directions the delay cancels exactly.
- **Round-trip delay** = `(t3 − t0) − (t2 − t1)`. If the delays are not
  equal, the offset is wrong by at most half of this, so it is the error bound.
- **Keep the fastest samples.** `estimate_skew` takes the 3 lowest round-trip
  samples, uses the median of their offsets and the fastest sample's half
  round trip as the error bound.
- **Alert only when the skew is beyond the threshold even in the best case**
  (`|offset| − error_bound > 0.5 s`), so network noise cannot page.
- **`drift_rate_ppm`** fits a line through offsets measured over days. A
  steady slope means the clock will drift again after a one-off correction.

In the tests (seeded fake network) the synced host produces 0 alerts across
100 checks of 8 samples, the host 180 s behind is estimated within 0.02 s and
alerts, and a 200 ppm drift over five days is measured within 1 ppm.

## Trade-offs
- The formula assumes roughly symmetric paths. A route that is always slower
  in one direction biases the estimate by up to half the round trip; the error
  bound makes that visible but cannot remove it.
- This **detects** skew; it does not fix it. Keep `chronyd`/`ntpd` running
  (and monitor that the daemon is up and synchronised), and prefer
  `chronyc tracking` or the node exporter's time metrics where available.
- Apps should still use monotonic clocks for timeouts and durations, and
  token checks need some leeway: detection reduces, not removes, skew.

## Run
```bash
python -m unittest -v test_solution.py
```

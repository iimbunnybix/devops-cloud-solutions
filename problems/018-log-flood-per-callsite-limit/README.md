# 018 · Payment errors vanished from the logs during an incident while the logging bill tripled

## Problem
A developer added `log.debug("item=%s", item)` inside the order worker's inner
loop to chase a bug and the change shipped with debug logging still enabled.
The worker wrote about 50,000 identical-looking lines per second. All services
share one log pipeline that ingests at most 5,000 lines per second per node;
once its buffer filled, it dropped every further line in that second. During a
card-processor outage that same evening, on-call searched for the payment
service's errors and found almost none: in the replay, only 2 of the 20
"card declined" errors per second made it through. Ingest volume (and the
vendor bill, which is priced per GB) went up for the whole week until someone
noticed.

## Root cause
Nothing limited how much a single line of code could log. The pipeline's
capacity is shared, so one runaway call site took the whole budget and the
drops fell on everyone, including the quiet services whose few lines mattered
most. Turning off debug logging fixes this one case, but the next hot-loop
`log.warning` would do the same.

## Solution
`CallSiteRateLimiter` sits in the logging library, before the shipper:

- Each **call site** (service + `file:line`) gets its own budget of
  `LINES_PER_WINDOW` = 20 lines per 1-second window. Code that logs a few
  lines is never touched; only the flood is cut.
- Lines over the budget are **counted, not shipped**. When the window ends,
  one summary line (`suppressed 49980 similar log lines`) is emitted, so the
  flood is still visible and alertable.
- `flush()` runs on a timer to emit summaries for call sites that went quiet
  after a burst, so no suppressed count is lost.

Replaying one second of the incident (50,000 debug lines + 20 payment errors):

| | Lines offered to the pipeline | Payment errors that survive |
|---|---|---|
| Original (ship everything) | 50,020 | 2 of 20 |
| Per-call-site limit | 40 | 20 of 20 |

## Trade-offs
- The first 20 lines of each window are kept and the rest dropped, so a rare
  but important value in line 21 is lost; the summary only says *how many*.
  Random sampling keeps a more representative set at the cost of determinism.
- Keying by call site means one logger that formats very different messages
  at the same line shares one budget. Keying by message template is finer
  but costs more memory.
- Memory grows with the number of distinct call sites seen in a window
  (usually thousands, not millions); `flush()` also starts new windows, so
  entries should be evicted for sites idle for a long time.
- This protects the pipeline from one process. Pipeline-side per-service
  quotas are still needed against a fleet of 500 pods each logging 20
  lines/second from the same site.

## Run
```bash
python -m unittest -v test_solution.py
```

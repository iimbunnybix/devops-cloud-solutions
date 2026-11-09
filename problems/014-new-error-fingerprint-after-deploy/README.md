# 014 · A deploy broke coupon checkout for two days, while the error-rate dashboard looked normal

## Problem
A checkout service handles about 100,000 requests per hour and always logs
around 2% errors: payment gateway timeouts, invalid addresses, cache misses
from the catalog. The on-call alert fires when the total error rate doubles.

A Tuesday release changed how coupons are loaded. Orders using one older
type of coupon now failed with `KeyError: 'discount_type'`, about 0.2% of
requests. The total error rate went from 2.0% to 2.2%, nowhere near the
alert threshold, and the error dashboard's "top messages" table was full of
one-off lines because every message contains an order id. The bug was found
two days later, from customer complaints and a drop in coupon redemptions.

## Root cause
- **Alerting on the total hides new error types.** A new error that is
  small compared to the existing background barely moves the sum. In the
  simulation the total rose by a factor of only **1.08**.
- **Raw messages cannot be grouped.** Messages include ids, amounts, emails,
  IPs and timings, so every line is unique: 2,019 errors in the baseline hour
  produced 2,019 distinct messages. "Is this error new?" could not be answered.

## Solution
`solution.py`:

1. **`template`** masks variable parts (UUIDs, emails, IPs, URLs, hex values,
   quoted strings, numbers with units, and ids of 8+ characters that mix
   letters and digits such as `ch_3MqLiJ2eZvKYlo2C` or `3f4e1a9c2b7d`) so that
   `invalid postcode '48213X' for user u8812@example.com` becomes
   `invalid postcode <str> for user <email>`.
2. **`fingerprint`** hashes service, exception type, template and top stack
   frame (with the line number removed, so a deploy that only moves code does
   not make every error look new). The 2,019 baseline errors collapse to 3
   fingerprints.
3. **`compare_windows`** compares the window after a deploy with a baseline
   window before it, normalised by request count:
   - **new**: a fingerprint not seen in the baseline, with at least
     `min_count` occurrences (5 by default);
   - **surging**: a known fingerprint whose rate per request grew by at
     least `surge_ratio` (3x by default). A traffic increase alone is not a
     surge.

   **Safety net:** events whose own fingerprint stays below `min_count` are
   pooled by service, exception type and frame (message ignored). If a
   message carries a variable part no mask recognises, every event gets its
   own fingerprint; without pooling, 300 such events would be 300 "one-offs"
   and the new error would never be reported. Pooled findings have
   `grouped=True`, and are skipped when the same error is already reported
   by its normal fingerprint.

Run as a post-deploy check (for example 30 minutes after each rollout, and as
a gate in the canary analysis, see 003), it reports exactly one finding for
the incident: the new `KeyError` in `checkout/pricing.py:apply_coupon`, 201
occurrences in the hour, with an example template, while the three known
error types are not reported.

## Trade-offs
- Masking rules are heuristics. Too aggressive (masking every word) merges
  different errors; too weak leaves ids in templates. Keep the rules in one
  place and add tests when a new log format appears. Structured logging
  (a constant message plus fields) removes most of the problem at the source.
- The pooling safety net is coarser: two different new errors raised from
  the same function with the same exception type are merged into one pooled
  finding. The `<id>` mask can also hide a meaningful token such as
  `sha256abc`; a short letters-only id is not masked at all and is left to
  the safety net.
- The baseline must be comparable: same time of day and traffic mix, or use
  several previous days. A daily batch job's errors look "new" if the
  baseline window missed the batch.
- `min_count` trades detection speed for noise. Low-traffic services need a
  longer window or a lower threshold.
- A fixed `surge_ratio` ignores statistical noise for small counts. A Poisson
  test on the counts is more precise if this produces false alarms.
- Error-tracking tools (Sentry and similar) do this grouping and "new issue
  in release" detection out of the box. This is the same idea for logs that
  do not go through such a tool.

## Run
```bash
python -m unittest -v test_solution.py
```

# 077 · Checkout returned errors for 20 minutes and the error-rate alert never fired

## Problem
An e-commerce team alerts on its checkout service with a Prometheus-style
rule: `rate(checkout_errors_total[1m]) > 5`, evaluated every 15 seconds. To
cut metrics cost, the platform team had changed the default scrape
interval from 15 seconds to 60 seconds a few weeks earlier.

During a bad payment-provider deploy, checkout failed about 12 times per
second for 20 minutes. Nobody was paged; customers reported it on social
media. Afterwards the graph panel for the alert's expression was empty for
the whole period, while the raw counter clearly climbed.

## Root cause
`rate()` needs **at least two samples** inside the window to compute a
slope. With a 60-second scrape interval, a 1-minute window contains only one
sample, so the expression returns *no data* at every evaluation, and "no
data" is not above the threshold. The rule had worked when scrapes happened
every 15 seconds (four samples per minute); the scrape-interval change
silently broke it.

In the reproduction (60 s scrapes, 12 errors/s), **100%** of evaluations of
the 1-minute rate have no data and the alert fires **0** times. A window of
just two scrape intervals is fragile too: one failed scrape leaves a single
sample and the rate disappears again.

## Solution
- `safe_rate_window(scrape_interval, requested)` raises the window to at
  least **4 scrape intervals** (240 s here), the usual guidance (it is how
  Grafana's `$__rate_interval` is sized). Even with a missed scrape the
  window keeps enough samples.
- With that window the alert fires at **every** evaluation during the
  outage and the computed rate is exactly 12 errors/s. A low error rate
  (1/s) still does not fire.
- `lint_rate_windows(rules, scrape_interval)` runs in CI over all alert
  rules and fails for any `rate()` window shorter than that minimum, so a
  future scrape-interval change shows up as a failed build, not a silent
  alert.

## Trade-offs
- A longer window reacts more slowly and smooths short spikes: a 4-minute
  window averages errors with the quiet minutes before them, so it
  crosses the threshold later than a short window would. Combine
  it with a `for:` duration you choose deliberately.
- The lint needs to know each target's scrape interval; jobs with
  different intervals need per-job checks.
- A separate "absent data" alert (see problem 031) catches the other way
  rules go silent: a target that stops being scraped.
- This model ignores counter resets and the extrapolation that real
  `rate()` applies at window edges; neither changes the two-sample rule.

## Run
```bash
python -m unittest -v test_solution.py
```

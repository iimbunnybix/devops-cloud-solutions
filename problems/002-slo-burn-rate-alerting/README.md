# 002 · On-call is paged 30 times a week for noise, yet missed a real 40-minute outage

## Problem
The checkout service alerts when "error rate > 1% for 5 minutes". On-call
engineers get paged for short blips that fix themselves, so they started
muting the alert. Last month a slow-burning failure (3% errors for 40
minutes) went unnoticed until customers complained on social media.

## Root cause
A static threshold on a short window measures **noise**, not **user
impact**. It cannot tell a 2-minute blip (harmless) from a sustained
problem that is eating the month's reliability budget.

## Solution
Alert on **SLO error-budget burn rate** (Google SRE Workbook, ch. 5):

- SLO: 99.9% of checkout requests succeed over 30 days, so the error budget is 0.1%.
- Burn rate = observed error rate ÷ 0.1%. Burn rate 1 spends the budget in
  exactly 30 days; 14.4 spends 2% of it in one hour.
- **Multi-window, multi-burn-rate** rules — each needs a long *and* a short
  window above threshold (the short window makes alerts stop quickly once fixed):

| Severity | Long window | Short window | Burn rate | Budget spent before firing |
|---|---|---|---|---|
| Page | 1 h | 5 min | 14.4 | 2% |
| Page | 6 h | 30 min | 6 | 5% |
| Ticket | 3 days | 6 h | 1 | 10% |

`solution.py` evaluates these rules over per-minute request/error counts.
Replaying the incidents (2,000 requests/minute):

| Scenario | Old rule (>1% for 5 min) | Burn-rate alerting |
|---|---|---|
| 2-minute blip at 20% errors | Pages (noise) | No page |
| 3% errors for 40 min (the missed outage) | Pages, but was muted | Pages at minute 29 |
| 10% errors | Pages | Pages at minute 9 |
| Total outage (50% errors) | Pages | Pages at minute 2 |

After a restart the alerter's in-memory history is shorter than the long
windows. Minutes it has not seen are counted as average traffic with zero
errors, so a 5-minute blip right after a deploy of the alerter is judged over
the full hour (no page), while a total outage still pages within 5 minutes.

Detection speed scales with severity, and pages stop within ~30 minutes of a fix
(the 6-hour rule's 30-minute short window).

## Trade-offs
- Needs enough traffic for error rates to be meaningful; low-traffic
  services need synthetic probes or longer windows.
- Treating unseen minutes as healthy is optimistic: an outage that started
  before the alerter restarted is under-counted until real history fills the
  windows. Persisting counters (or reading them from the metrics store) avoids this.
- Burn-rate alerts measure user impact; still keep a few cause-based alerts
  (e.g. disk 95% full) for problems that hit users suddenly.

## Run
```bash
python -m unittest -v test_solution.py
```

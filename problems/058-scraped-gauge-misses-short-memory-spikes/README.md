# 058 · Workers kept getting OOM-killed while the memory dashboard showed a flat 45% and the 90% alert never fired

## Problem
A fleet of report-generation workers runs in containers with a hard memory
limit. Prometheus scrapes each worker's `memory_usage_percent` gauge every 60
seconds and an alert fires at 90%.

After a new batch feature shipped, workers started restarting several times
an hour with exit code 137 (killed by the kernel OOM killer). The memory
graph was flat at about 45%, the alert never fired, and the team spent two
days looking at the network and the scheduler. The real cause was that each
batch pushed memory to the limit for a few seconds while it built a large
report, then dropped back.

In the simulation in the tests, five 4-second spikes to 98% in ten minutes
are invisible to a 60-second scrape (all ten scrapes read 45%), and scraping
every 15 seconds does not help either: the spikes still fall between scrapes.

## Root cause
A gauge is sampled, not accumulated: the scrape only sees the value at the
instant it runs. Anything shorter than the scrape interval is seen only if a
scrape happens to land on it. `max_over_time()` in the query cannot recover
it either, because the stored samples never contained the spike. Scraping
more often lowers the odds of a miss but never removes them, and multiplies
storage cost.

## Solution
Track the peak inside the process, where every change is visible.
`PeakSinceLastScrapeGauge`:

- On every update, keeps `peak = max(peak, value)`, a single comparison.
- On scrape, returns the peak and **resets it to the current value** (not
  to 0), so a value that stays high is still reported on the next scrape.

The exported value is now the **maximum of the whole interval**. In the
tests every minute that contained a spike reports 98%, a single 1-second
spike is caught, a spike that crosses a scrape boundary shows up in both
intervals, and the alert fires.

## Trade-offs
- Resetting on scrape assumes a single scraper. With two Prometheus replicas
  each one resets the peak for the other; use a time-windowed peak (for
  example the max over the last 60 s, kept in a small ring buffer) instead.
- For memory specifically, the kernel already keeps a peak: cgroup v2
  `memory.peak` and the container OOM-kill counters (`oom_kill` in
  `memory.events`). Alerting on OOM kill events directly is the most
  reliable signal.
- Peaks tell you *that* a spike happened, not its shape; for diagnosis add a
  histogram or profile the batch job.
- The real fix for the workers was streaming the report instead of building
  it in memory; the metric fix is what makes such problems visible.

## Run
```bash
python -m unittest -v test_solution.py
```

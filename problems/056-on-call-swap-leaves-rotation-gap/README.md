# 056 · A database alert paged at 02:14 on Sunday and nobody answered for seven hours

## Problem
A platform team of seven runs a weekly on-call rotation: one 24-hour shift
each, handing off at 09:00 UTC. Fay could not cover her Saturday, so Bob
agreed to take it. The swap was entered by deleting Fay's shift and adding an
override for Bob, but the scheduling tool's override form defaulted to 12
hours and nobody changed it. Bob was on call from Saturday 09:00 to 21:00, and
then nobody was until Gus took over on Sunday at 09:00.

At 02:14 on Sunday the primary database's disk filled up. The page went to an
empty slot and escalated to nobody. Writes failed until 09:00, when Gus saw
the alert at handoff: about seven hours of failed orders and a weekend of
manual replays. The schedule had passed its weekly check the Friday before.

## Root cause
The pre-publish check (`naive_every_day_covered`) asked "does every calendar
day have at least one shift touching it?". Saturday had Bob's 12-hour override
and Sunday had Gus's shift, so the answer was yes. Coverage is a property of
**time**, not of days: a schedule can touch every day and still leave hours
uncovered. Swaps, overrides and deletions are exactly the edits that create
such holes, and nothing compared the edited schedule against the window it was
supposed to cover.

## Solution
`find_gaps` treats every shift (base rotation and overrides alike) as a
half-open interval `[start, end)`, sorts them, merges overlapping or touching
intervals, and walks the window from start to end reporting every uncovered
stretch, including before the first shift and after the last one.
`validate_schedule` adds a check for shifts whose end is not after their start
(an easy slip with swapped fields) and returns readable messages in UTC, such
as `nobody on call 2026-11-14 21:00 to 2026-11-15 09:00 UTC (12.0h)` for the
incident schedule. The schedule is only published when the list is empty.

Gaps shorter than one minute are ignored, because hand-entered times like
20:59:30 versus 21:00 are noise, not holes worth blocking a publish for.

## Trade-offs
- This proves that *someone* is scheduled, not that they will answer. Keep an
  escalation policy with a secondary and a manager tier, and alert when a page
  is unacknowledged.
- Merging ignores who is on call, so a shift assigned to someone who has left
  the team still counts as coverage. Validate people against the team roster.
- Run the check on every schedule change and nightly for the next few weeks,
  not just once a week: overrides are usually added at short notice.
- Many scheduling tools already show gaps; this check is still useful when the
  schedule is generated as code or synced from a spreadsheet.

## Run
```bash
python -m unittest -v test_solution.py
```

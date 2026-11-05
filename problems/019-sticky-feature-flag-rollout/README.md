# 019 · Shoppers saw the checkout page flip between the old and new design on every reload during a "20%" rollout

## Problem
The web team shipped a redesigned checkout behind a feature flag set to
"20% of users". Within an hour support had tickets saying the checkout page
"keeps changing": a shopper would see the new design, reload, get the old one,
and lose the address they had typed because the two versions stored the form
differently. The A/B dashboard showed nearly identical conversion for both
groups, because almost every shopper had been in both.

## Root cause
The flag check drew a fresh random number on every request
(`random() * 100 < 20`). Nothing tied the decision to the user, so each
request was an independent coin flip. With 10 requests per session, a user
stays in one group only with probability 0.8¹⁰ + 0.2¹⁰ ≈ 11%; the simulation
in the tests shows **89.7%** of 10,000 users seeing both versions
(theory: 89.3%).

## Solution
`is_enabled(flag, user_id, percent)` hashes `flag + "\0" + user_id` with
SHA-256 into one of 10,000 buckets and enables the user when their bucket is
below `percent × 100`:

- **Sticky**: the same user always gets the same answer, on every server, with
  no shared state or database lookup.
- **Monotonic ramp**: going 1% → 5% → 25% → 100% only moves the cut-off up, so
  nobody who already has the new design loses it.
- **Even spread**: 20% enabled 20.03% of 100,000 test users; 0.5% (steps of
  0.01% are possible) is also accurate.
- **Independent flags**: the flag name is part of the hash, so two flags at 10%
  overlap on 0.96% of users (≈ 10% × 10%), instead of the same "unlucky" 10%
  getting every experiment first.
- The `\0` separator stops `("ab", "c")` and `("a", "bc")` from hashing the
  same input.

## Trade-offs
- Anonymous visitors need a stable ID (a first-party cookie); if it is
  cleared, they can switch groups once.
- Lowering the percentage removes the most recently added users, and changing
  the flag name re-shuffles everyone. Rename only when you want a fresh sample.
- Hash bucketing can't target "these 50 beta customers"; real flag systems
  (LaunchDarkly, Unleash, OpenFeature providers) add allow-lists and attribute
  rules on top of the same bucketing idea.
- SHA-256 is slower than a non-cryptographic hash (e.g. MurmurHash), but it is
  in the standard library and still costs microseconds per check.

## Run
```bash
python -m unittest -v test_solution.py
```

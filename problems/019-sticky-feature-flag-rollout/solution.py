"""Keep each user on the same side of a percentage feature-flag rollout.

The new checkout UI was rolled out to "20% of users" by drawing a random number
on every request. A user who reloads the page lands in a different group each
time, so the UI flips between old and new mid-session, carts built in one UI
break in the other, and the experiment's metrics are meaningless. This module
assigns every (flag, user) pair a stable bucket from a hash, so the decision is
the same on every request and every server, and raising the percentage only
ever adds users to the new group.
"""
from __future__ import annotations

import hashlib
import random

# 10,000 buckets lets percentages be set in steps of 0.01%.
BUCKET_COUNT = 10_000
BUCKETS_PER_PERCENT = BUCKET_COUNT // 100
# The first 8 bytes of SHA-256 give a 64-bit number, far more than enough
# precision for an even spread over 10,000 buckets.
HASH_BYTES_USED = 8


def naive_is_enabled(rollout_percent: float, rng: random.Random) -> bool:
    """The original check: a fresh coin flip on every request (the bug)."""
    return rng.random() * 100 < rollout_percent


def validate_percent(rollout_percent: float) -> None:
    """Reject percentages outside 0-100 instead of silently clamping them."""
    if not 0 <= rollout_percent <= 100:
        raise ValueError(f"rollout percent must be between 0 and 100, got {rollout_percent}")


def bucket_for(flag_name: str, user_id: str) -> int:
    """Return the user's fixed bucket (0 to BUCKET_COUNT - 1) for this flag.

    The flag name is part of the hash input so that the users who get flag A
    first are not the same users who get every other flag first.
    """
    # A separator that cannot appear in flag names keeps ("ab", "c") and
    # ("a", "bc") from hashing to the same input.
    key = f"{flag_name}\x00{user_id}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    number = int.from_bytes(digest[:HASH_BYTES_USED], "big")
    return number % BUCKET_COUNT


def is_enabled(flag_name: str, user_id: str, rollout_percent: float) -> bool:
    """Decide the flag for this user; the same inputs always give the same answer.

    A user is enabled when their bucket falls below the rollout cut-off. Raising
    the percentage moves the cut-off up, so everyone already enabled stays
    enabled: the ramp is monotonic.
    """
    validate_percent(rollout_percent)
    # round() so 0.29% gives 29 buckets, not 28 from float error.
    enabled_buckets = round(rollout_percent * BUCKETS_PER_PERCENT)
    return bucket_for(flag_name, user_id) < enabled_buckets


def enabled_fraction(flag_name: str, user_ids: list[str], rollout_percent: float) -> float:
    """Share of the given users who get the flag (used to check the spread)."""
    enabled_count = 0
    for user_id in user_ids:
        if is_enabled(flag_name, user_id, rollout_percent):
            enabled_count += 1
    return enabled_count / len(user_ids)

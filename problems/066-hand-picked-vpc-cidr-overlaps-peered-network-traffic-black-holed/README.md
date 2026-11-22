# 066 · After peering the new analytics VPC, connections to the office VPN and a partner network timed out

## Problem
A data team needed a new VPC for an analytics cluster. The engineer picked
`10.20.0.0/16` from an old wiki page of "free ranges" and checked that no
other VPC in the account had exactly that CIDR. The VPC was created, peered
with the shared network hub, and routes were added.

Within minutes, nightly jobs that pulled files from a partner over the
peered link started timing out, and engineers on the office VPN could no
longer reach the analytics dashboards. The office VPN used `10.20.8.0/21`
and the partner's peered VPC used `10.20.64.0/18`, both inside the new
`/16`. Rebuilding the VPC with a different range took a day, because every
subnet, instance and security group in it had to be recreated.

## Root cause
Two networks conflict whenever they share **any** address, not only when
their CIDRs are identical. The check compared strings for equality, and
the wiki was not the source of truth for VPN and partner ranges. Inside a
VPC the local route for its own CIDR is the most specific match for those
addresses, so packets for the VPN and the partner never left the VPC.

In `solution.py`, `naive_accept("10.20.0.0/16", ...)` returns `True` even
though `find_overlaps` shows it overlaps `10.20.8.0/21 (vpn:office)` and
`10.20.64.0/18 (peer:partner)`.

## Solution
- **One registry of used ranges.** Own VPCs, peered VPCs, VPN and on-prem
  networks are all recorded with an owner, because any of them can collide.
- **Real overlap check.** `validate` uses `IPv4Network.overlaps`, which is
  true for partial overlap and containment in either direction, and lists
  every conflict with its owner. CIDRs with host bits set
  (`10.20.1.0/16`) are rejected instead of being rounded silently, because
  they usually mean the author had another range in mind.
- **Allocate, don't pick.** `allocate` walks the aligned blocks of the
  requested size inside the company supernet in address order and returns
  the first one that overlaps nothing. With the test registry the first free
  `/16` in `10.0.0.0/8` is `10.2.0.0/16`, and a `/21` inside
  `10.20.0.0/16` fills the gap below the VPN (`10.20.0.0/21`, then
  `10.20.16.0/21`). `reserve` records the block so the next request gets a
  different one.

## Trade-offs
- The allocator is only as good as the registry. Feed it from the cloud API
  (VPCs, peerings, VPN routes) and from the network team's on-prem list,
  not from a hand-edited page. Cloud IPAM services (for example AWS VPC
  IPAM) do this with account-wide locking.
- First-fit is simple and predictable but can fragment the space over
  time; allocating each team a larger block up front and sub-allocating
  inside it keeps ranges aggregatable for route summaries.
- Two engineers allocating at the same moment can get the same block;
  in production the reserve step needs a lock or a compare-and-set on the
  registry.
- If overlap with a partner cannot be avoided (both sides already use
  `10.0.0.0/8`), the remaining options are NAT between the networks or a
  service endpoint (PrivateLink-style) instead of routing.

## Run
```bash
python -m unittest -v test_solution.py
```

# 034 · Half the fleet ran a different build of the same release, and rolling back did not bring the old code back

## Problem
An online shop deploys its API container to a fleet of hosts with a
manifest that names the image by tag, e.g.
`registry.internal:5000/shop/api:v2`. Each host pulls the image when the
rollout reaches it.

During one rollout of `v2`, CI pushed a hotfix build and tagged it `v2`
again. Hosts that had already pulled kept build 101; hosts updated later
got build 102. For two days the same "release 2.0" behaved differently
depending on which host served the request, and bug reports could not be
reproduced.

A week later a bad build went out under `:latest`. The on-call engineer
"rolled back" by redeploying the previous manifest, which also said
`:latest`. The tag now pointed at the bad build, so every host pulled it
again; the rollback changed nothing and the outage continued until
someone found the old build by hand.

## Root cause
A tag is a **mutable pointer**; a registry lets anyone move it to a new
image. The manifest stored the pointer, not the image, and each host
resolved it at the moment it pulled. So:

- Hosts that pulled at different times ran different code under one
  release (in the reproduction, a rollout of 6 nodes with the tag moved
  halfway ends with **2 different digests**).
- A release could not be reproduced later: "release 1" meant "whatever
  `:latest` is now", so rolling back to it redeployed the bad build.

## Solution
`solution.py` resolves tags **once, at release time**, and stores digests:

1. `create_pinned_release` asks the registry for each tag's content
   digest and writes `repo@sha256:...` into the release. An unknown tag
   fails right there, not halfway through a rollout.
2. Nodes pull the digest. A digest identifies the image bytes, so
   re-tagging cannot change what a release means. The rollout with the
   tag moved halfway ends with **1 digest** on all 6 nodes, and rolling back
   to release 1 restores exactly the earlier good build.
3. `find_drifted_nodes` compares every node's running digest with the
   release's digest and lists the ones that differ (someone pulled a tag by
   hand, or a node missed the rollout). Run it after each rollout and on a
   schedule.

`split_reference` handles registry ports (`host:5000/repo` has no tag),
the implicit `latest` tag, and `repo:tag@digest` (the digest wins, as in
Docker).

## Trade-offs
- Pinned manifests are harder to read than `:v2`. Keep the original tag
  next to the digest in release notes or as an annotation.
- Base-image security fixes no longer arrive "for free" when a tag is
  updated; you need a pipeline that rebuilds and creates a new release.
  That is the point: changes go through a release, not a silent re-tag.
- Registries can also enforce **immutable tags** (several hosted
  registries support it). That stops re-tagging but does not help with
  `:latest`, and digests remain the only reference that can be verified
  on the node.
- Old digests must be kept in the registry for as long as you may roll
  back to them; registry garbage collection policies have to respect
  that.

## Run
```bash
python -m unittest -v test_solution.py
```

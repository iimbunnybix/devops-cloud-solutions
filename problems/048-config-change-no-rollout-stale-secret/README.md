# 048 · Checkout failed every payment with 401 an hour after a "successful" API key rotation

## Problem
The checkout service runs 6 pods on Kubernetes. Its payments API key lives in
a Secret/ConfigMap (`checkout-config`) that the pods read once at start-up.
The quarterly rotation runbook says: write the new key to the config store,
re-apply the deployment, check that the pipeline is green, then revoke the
old key at the payment provider.

Every step reported success. The re-apply printed `deployment unchanged`,
which nobody read as a problem. An hour later the old key was revoked, and
every payment call from every pod returned **401 Unauthorized**. Checkout was
down until someone ran `kubectl rollout restart` by hand. The config store
had held the correct key the whole time.

## Root cause
The deployment template referred to the config only by name
(`checkout-config`). Rotating the key changed the *values* behind that name,
not the name, so the rendered deployment spec was byte-for-byte identical.
The orchestrator only rolls out when the spec changes, so nothing restarted
and the pods kept the key they had read at start-up. In the replay,
re-applying the old template after rotation causes **0** rollouts and all
**6 of 6** pods fail once the old key is revoked.

This is not config *drift* between declared and actual infrastructure (006)
or a half-written config file (045): the store was correct and complete; the
change simply never reached the running process.

## Solution
`solution.py`:

1. **`render_deployment`** puts a checksum of the rendered config into the
   spec as the annotation `checksum/config` (the same pattern as Helm's
   `checksum/config: {{ sha256sum ... }}`). Rotating the key changes the
   checksum (`e98cc9d36728` to `da1a31bdf30c` in the test), so the spec
   differs and a normal, gradual rollout starts: no manual restarts, and the
   usual health checks and rollback still apply.
2. **`render_config`** serialises with sorted keys, so reordering keys in the
   store does not cause a pointless restart, and an unchanged config never
   restarts pods.
3. **`stale_pods`** / **`drift_report`** compare the config hash each running
   pod reports (for example via a metric label or a `/version` endpoint) with
   the hash of the config in the store. Run before revoking an old key, it
   would have shown 6 pods on the old hash and 0 on the new one. Revoke only
   when the old hash has no pods left.

## Trade-offs
- The checksum must cover every config object the pod reads; a second Secret
  left out of the hash has the same bug. Tools like Stakater Reloader watch
  the objects instead, at the cost of another controller in the cluster.
- Every config change now restarts pods, including harmless ones like a log
  level. Apps that hot-reload config can skip restarts, but then the drift
  check is the only proof the new value is live.
- A short unsalted hash of a low-entropy secret can be guessed by brute force.
  For secrets, prefer hashing a version ID from the secret manager, or an
  HMAC with a key the cluster holds, over hashing the raw value.
- Immutable, versioned config names (`checkout-config-v7`) give the same
  effect and make rollback point at the old config, but need cleanup of old
  versions.
- The safest rotation keeps both keys valid until the drift check shows no
  pod on the old one; the checksum makes that happen sooner, not instantly.

## Run
```bash
python -m unittest -v test_solution.py
```

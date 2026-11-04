# DevOps & Cloud Solutions

Real reliability and operations problems (retry storms, alerting, deployments, cost, observability), each with root cause, a working Python fix and tests.

Every problem folder has the same structure:

| File | Contents |
|---|---|
| `README.md` | The incident or requirement, root cause, solution and trade-offs |
| `solution.py` | Working implementation (Python standard library only) |
| `test_solution.py` | Tests that reproduce the problem and prove the fix |

## Problems

<!-- INDEX:START -->
| # | Problem |
|---|---|
| 001 | [Database recovers from a 2-minute blip, then goes down again under a retry storm](problems/001-retry-storm-backoff-jitter) |
| 002 | [On-call is paged 30 times a week for noise, yet missed a real 40-minute outage](problems/002-slo-burn-rate-alerting) |
| 003 | [Canary passed its health gate, then the full rollout quadrupled checkout errors](problems/003-canary-analysis-gate) |
<!-- INDEX:END -->

## Run the tests

```bash
python run_tests.py                       # all problems
cd problems/<folder> && python -m unittest -v
```

Requires Python 3.10+. No third-party packages.

## Author

Hrishikesh Jadhav

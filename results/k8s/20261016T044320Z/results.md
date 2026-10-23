# Part D - Kubernetes failure experiments (20261016T044320Z, context `kind-aitu`)

| # | Injected failure | Detection time | Recovery time | Failed / total requests | Visible outage |
|---|---|---|---|---|---|
| 1 | One Registration replica fails | 0.14 s | 3.41 s | 0 / 37 (0.0%) {} | 0.0 s |
| 2 | All Registration replicas fail | 0.13 s | 3.0 s | 7 / 31 (22.6%) {'0': 7} | 1.25 s |
| 3 | Database unavailable (60 s) | 9.92 s | 64.98 s | 79 / 99 (79.8%) {'503': 19, '0': 60} | 63.89 s |

HTTP code 000 = connection failed / timed out (no endpoint answered).

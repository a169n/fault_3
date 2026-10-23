# Part D - Kubernetes failure experiments (20261023T104630Z, context `kind-aitu`)

| # | Injected failure | Detection time | Recovery time | Failed / total requests | Visible outage |
|---|---|---|---|---|---|
| 1 | One Registration replica fails | 0.11 s | 3.02 s | 0 / 35 (0.0%) {} | 0.0 s |
| 2 | All Registration replicas fail | 0.14 s | 3.05 s | 7 / 31 (22.6%) {'0': 7} | 1.25 s |
| 3 | Database unavailable (60 s) | 10.03 s | 64.76 s | 80 / 101 (79.2%) {'503': 20, '0': 60} | 63.86 s |

HTTP code 000 = connection failed / timed out (no endpoint answered).

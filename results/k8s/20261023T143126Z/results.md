# Part D - Kubernetes failure experiments (20261023T143126Z, context `kind-aitu`)

| # | Injected failure | Detection time | Recovery time | Failed / total requests | Visible outage |
|---|---|---|---|---|---|
| 3 | Database unavailable (60 s) | 8.54 s | 63.58 s | 80 / 100 (80.0%) {'503': 19, '0': 61} | 62.62 s |

HTTP code 000 = connection failed / timed out (no endpoint answered).

# Part D - Kubernetes failure experiments (20261023T143029Z, context `kind-aitu`)

| # | Injected failure | Detection time | Recovery time | Failed / total requests | Visible outage |
|---|---|---|---|---|---|
| 2 | All Registration replicas fail | 0.14 s | 3.41 s | 8 / 33 (24.2%) {'0': 8} | 1.47 s |

HTTP code 000 = connection failed / timed out (no endpoint answered).

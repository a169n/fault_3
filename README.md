# Assignment 3 — Cloud Computing and Microservices Resilience (AITU, synthetic data)

Project for Assignment 3: a synthetic AITU Digital Student Services platform.
All data, endpoints and credentials are educational and made up.

## What is where

| Assignment part | Files |
|---|---|
| **Report (template)** | `report/REPORT.md` and `report/REPORT.docx` (complete the **[FILL IN]** fields yourself) |
| Part A — architecture analysis, diagram | `docs/architecture.png` / `.svg`, table in the report (section 3.1) |
| Part B — availability calculation | `scripts/availability.py` → `results/availability.md`, reliability block diagram `docs/rbd.png` |
| Part C — Kubernetes YAML | `k8s/11-registration-deployment.yaml`, `k8s/12-registration-service.yaml`, `k8s/13-registration-pdb.yaml` |
| Part D — failure simulation | `scripts/simulate_failures.py` → `results/simulation_*`; on a real cluster: `scripts/k8s/run_experiments.py` |
| Part E — RPO/RTO and DR | `docs/dr-plan.md`, `k8s/20-db-backup-cronjob.yaml`, `scripts/dr_drill.sh` → `results/dr_drill.log` |
| Oral defense notes (Russian) | `docs/defense_notes_ru.md` |
| The service itself | `app/registration_service.py` (Python, standard library only), `app/Dockerfile` |

```
app/        registration service and Dockerfile
k8s/        manifests: namespace, secret, postgres, configmap, deployment, service, PDB, backup CronJob
k8s/tools/  load generator (traffic for the cluster experiments)
scripts/    calculations, simulator, DR drill, DOCX build, kind scripts
docs/       diagrams, DR plan, defense notes
results/    measured results and logs
report/     the report
```

## Key results

* **Part B.** End-to-end availability is **99.8417%**, so the 99.9% target is **not met**. The bottleneck is the single API Gateway (99.90%). With 2 gateways it becomes **99.9415%**; with 2 LBs and 2 gateways, 99.9915%.
* **Part D (simulation).**

  | Experiment | Detection | Recovery | User impact |
  |---|---|---|---|
  | One replica fails | 0.92 s | 2.99 s | 9 of 120 requests failed |
  | Both replicas fail | 0.95 s | 3.0 s to the first replica, 13 s to both | 60 requests failed |
  | Database down for 30 s | 9.04 s | 4.2 s after the DB returned | 503 for the whole outage, 0 restarts |

* **Part E (DR drill).** After `DROP TABLE`, restoring from `pg_dump` recovered 1,000 of 1,250 rows, while point-in-time recovery (PITR) from the WAL archive recovered 1,250 of 1,250.

## How to run on macOS

You need Python 3 (it comes with the Xcode Command Line Tools, or `brew install python`).

### 1. Availability calculation (Part B)

```bash
python3 scripts/availability.py
```

### 2. Failure simulation without Kubernetes (Part D), about 2 minutes

```bash
python3 scripts/simulate_failures.py            # all 4 experiments
python3 scripts/simulate_failures.py --only 1 2 # selected experiments only
```

Results are written to `results/simulation_results.md`. To read the probe settings directly from the YAML, install PyYAML (`pip3 install pyyaml`). Without it the simulator uses the same values as defaults.
**Run it yourself:** the "Individual work" section of the report requires your own run.

### 3. Real Kubernetes on a Mac (kind)

```bash
brew install kind kubectl          # plus Docker Desktop (or colima: brew install colima docker && colima start)
bash scripts/k8s/setup_kind.sh     # 3-node cluster, image build, deploy
kubectl -n aitu-sss get pods -o wide
python3 scripts/k8s/run_experiments.py --db-outage 60   # experiments 1–3 on the cluster
bash scripts/k8s/teardown_kind.sh  # delete the cluster
```

Results are saved to `results/k8s/<timestamp>/`: a results table, the load log, events and a timeline.
For screenshots in the report, keep `kubectl -n aitu-sss get pods -w` open in a second terminal during the experiments.

With minikube instead: `minikube start --nodes 2`, then `minikube image build -t aitu-demo/registration-service:1.0.0 app/`, `kubectl apply -f k8s/`, and the same `run_experiments.py`.

Check the Service manually:

```bash
kubectl -n aitu-sss run tmp --rm -it --image=curlimages/curl:8.10.1 --restart=Never -- \
  curl -s -XPOST -H 'Content-Type: application/json' \
  -d '{"student_id":"S0001","course_code":"CC-301"}' http://registration-service/api/v1/registrations
```

Liveness demo (a hung pod is restarted):

```bash
POD=$(kubectl -n aitu-sss get pod -l app=registration-service -o name | head -1)
kubectl -n aitu-sss exec $POD -- python -c "import urllib.request as u; u.urlopen(u.Request('http://localhost:8080/chaos/hang', method='POST'))"
kubectl -n aitu-sss get pods -w     # after ~30 s RESTARTS becomes 1
```

### 4. DR drill (Part E), requires a local PostgreSQL

```bash
brew install postgresql@16
export PGBIN="$(brew --prefix postgresql@16)/bin"
bash scripts/dr_drill.sh           # log in results/dr_drill.log
```

### 5. YAML validation and report build

```bash
brew install kubeconform pandoc && pip3 install python-docx
kubeconform -strict -summary k8s/*.yaml k8s/tools/*.yaml
python3 scripts/build_report_docx.py     # report/REPORT.md -> report/REPORT.docx
```

## How the results in `results/` were produced

* Kubernetes could not run in the cloud sandbox where this project was prepared (nested containers are not supported there). Part D was therefore run with the **simulator** `scripts/simulate_failures.py`. It runs the real service code as two processes and emulates the kubelet: probes with the parameters from the YAML, restarts, CrashLoopBackOff. It also emulates Service load balancing over Ready pods and a database outage. The report states this openly as a limitation.
* The YAML was validated with `kubeconform` against the Kubernetes 1.31 schemas: 11 resources, 0 errors.
* The service was tested separately against a real PostgreSQL 16 (`results/postgres_backend_check.log`). When the DB is down, readiness returns 503 while liveness stays 200; when the DB comes back, the connection is re-established automatically.
* The DR drill was run on a real PostgreSQL 16 (`results/dr_drill.log`).

## To do before submission

1. Complete every **[FILL IN]** in `report/REPORT.md`: name, ID, group, variant, dates, commit. Then rebuild the DOCX or edit `report/REPORT.docx` directly.
2. Run the simulator (and, if possible, kind) on your own Mac. Attach your own logs and screenshots.
3. Rewrite "problem I encountered" and "unique decision" in your own words.
4. Commit and put the commit hash in the report.
5. Read `docs/defense_notes_ru.md`.

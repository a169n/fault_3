# Assignment 3 — Cloud Computing and Microservices Resilience (AITU, synthetic data)

Project for Assignment 3: a synthetic AITU Digital Student Services platform.
All data, endpoints and credentials are educational and made up.

## What is where

| Assignment part | Files |
|---|---|
| **Report (template)** | `docs/REPORT.pdf` / `.docx` (instructor's template, filled in); `docs/REPORT.md` is the same content in Markdown; screenshots of the run in `docs/screenshots/` |
| Part A — architecture analysis, diagram | `docs/architecture.png`, table in the report (section 3.1) |
| Part B — availability calculation | `scripts/availability.py` → `results/availability.md`, reliability block diagram `docs/rbd.png` |
| Part C — Kubernetes YAML | `k8s/11-registration-deployment.yaml`, `k8s/12-registration-service.yaml`, `k8s/13-registration-pdb.yaml` |
| Part D — failure experiments | `scripts/k8s/run_experiments.py` on a kind cluster → `results/k8s/<timestamp>/` |
| Part E — RPO/RTO and DR | `docs/dr-plan.md`, `k8s/20-db-backup-cronjob.yaml`, `scripts/dr_drill.sh` → `results/dr_drill.log` |
| Oral defense notes (Russian) | `docs/defense_notes_ru.md` |
| The service itself | `app/registration_service.py` (start-up, graceful shutdown), `app/config.py` (settings), `app/store.py` (PostgreSQL), `app/handlers.py` (HTTP endpoints); Python standard library + the pure-Python `pg8000` driver; `app/Dockerfile` |

```
app/        registration service and Dockerfile
k8s/        manifests: namespace, secret, postgres, configmap, deployment, service, PDB, backup CronJob
k8s/tools/  load generator (traffic for the cluster experiments)
scripts/    calculations, DR drill, kind cluster + experiment scripts
docs/       the report (PDF/DOCX/MD), diagrams, screenshots, DR plan, defense notes
results/    measured results and logs
```

## Key results

* **Part B.** End-to-end availability is **99.8417%**, so the 99.9% target is **not met**. The bottleneck is the single API Gateway (99.90%). With 2 gateways it becomes **99.9415%**; with 2 LBs and 2 gateways, 99.9915%.
* **Part D (kind cluster, `results/k8s/20261023T104630Z/`).**

  | Experiment | Detection | Recovery | User impact |
  |---|---|---|---|
  | One replica fails | 0.11 s | 3.02 s | 0 of 35 requests failed |
  | Both replicas fail | 0.14 s | 3.05 s | 1.25 s outage, 7 of 31 requests failed |
  | Database down for 60 s | 10.03 s | 64.76 s (≈5 s after the DB returned) | unavailable for the whole outage, 0 restarts |
  | One replica hangs (liveness) | ≈29 s | ≈34 s | none |

* **Part E (DR drill).** After `DROP TABLE`, restoring from `pg_dump` recovered 1,000 of 1,250 rows, while point-in-time recovery (PITR) from the WAL archive recovered 1,250 of 1,250.

## How to run on macOS

You need Python 3 (it comes with the Xcode Command Line Tools, or `brew install python`).

### 1. Availability calculation (Part B)

```bash
python3 scripts/availability.py
```

### 2. Failure experiments on Kubernetes (Part D, kind on a Mac)

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

### 3. DR drill (Part E), requires a local PostgreSQL

```bash
brew install postgresql@18
export PGBIN="$(brew --prefix postgresql@18)/bin"
bash scripts/dr_drill.sh           # log in results/dr_drill.log
```

### 4. YAML validation

```bash
brew install kubeconform
kubeconform -strict -summary k8s/*.yaml k8s/tools/*.yaml
```

## How the results in `results/` were produced

* Part D was run on a local kind cluster (kind v0.33, Kubernetes v1.37, 1 control plane + 2 workers, Docker Desktop on macOS) with `scripts/k8s/run_experiments.py --db-outage 60`; experiment 4 (liveness) with the commands above, log in `liveness_hang.txt`.
* The YAML was validated with `kubeconform` against the Kubernetes 1.31 schemas: 11 resources, 0 errors.
* The service was tested separately against a real PostgreSQL 16 (`results/postgres_backend_check.log`). When the DB is down, readiness returns 503 while liveness stays 200; when the DB comes back, the connection is re-established automatically.
* The DR drill was run on a local PostgreSQL 18 (`results/dr_drill.log`).

## To do before submission

1. Fill in Student ID, group and variant in `docs/REPORT.docx` (still empty) and export it to PDF.
2. Run the kind experiments on your own Mac. Attach your own logs and screenshots of `kubectl get pods -w`.
3. Rewrite "problem I encountered" and "unique decision" in your own words.
4. Commit and put the commit hash in the report.
5. Read `docs/defense_notes_ru.md`.

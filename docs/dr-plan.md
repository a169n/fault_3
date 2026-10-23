# Part E — RPO/RTO and Disaster Recovery plan

Targets from the assignment: **RPO ≤ 5 min**, **RTO ≤ 15 min**. All data is synthetic.

## 1. RPO/RTO plan

| Mechanism | Frequency | Expected RPO | Recovery time | Purpose |
|---|---|---|---|---|
| **Database backup**: base backup plus continuous WAL archiving to object storage in another zone (pgBackRest / WAL-G / CloudNativePG Barman). In the lab: `pg_dump` CronJob every 5 min (`k8s/20-db-backup-cronjob.yaml`). | Full base backup daily at 02:00. WAL streamed continuously, with `archive_timeout = 60 s`. Logical dump every 5 min as a second copy. | **≤ 1 min** (WAL). ≤ 5 min (dump). | 5–10 min to restore the base backup and replay WAL to a point in time. Lab drill: 0.52 s for 1,250 rows. | Protects against data loss and corruption: human error (`DROP TABLE`), a bad migration, ransomware, or losing both DB nodes. |
| **Replica**: PostgreSQL streaming replication to a standby node in another zone, with automatic failover (Patroni or CloudNativePG). | Continuous | ≈ 0–5 s (asynchronous). 0 with synchronous commit. | 30–60 s automatic failover. Clients reconnect through the `postgres` Service DNS name. | **High availability**: survive the loss of one DB node or zone without restoring anything. |
| **Configuration backup**: all Kubernetes YAML in Git (GitOps with Argo CD or Flux), secrets in a secret manager, etcd snapshot. | On every change (git commit). etcd snapshot hourly. | 0 for everything in Git | 3–5 min (`kubectl apply -f k8s/` or an Argo CD sync to a new cluster) | Rebuild the platform from scratch. Roll back a bad configuration change (`kubectl rollout undo`). |
| **Full recovery**: DR runbook (section 3). A warm standby cluster in a second zone or region. | DR drill every semester, before the registration period | **≤ 5 min** (last archived WAL segment) | **≤ 15 min**: about 2 detect/declare + 3 cluster + 2 apply + 5 DB PITR + 2 DNS switch = 14 min | Recover from losing the whole cluster, data centre or region. |

### Why these frequencies

* A dump every 5 minutes meets the 5-minute RPO only for a small database. It also adds load every 5 minutes, and a restore loses everything written since the last dump. The lab drill confirmed this: **250 of 1,250 rows were lost** when restoring from `pg_dump`.
* WAL archiving makes RPO independent of backup frequency. Every committed transaction is in a WAL segment, and `archive_timeout = 60 s` forces a segment switch at least once a minute. In the drill, point-in-time recovery restored **1,250 of 1,250 rows**, up to 1 s before the `DROP TABLE`.
* The 15-minute RTO is not achievable if a new cluster has to be created from nothing (that takes 10–20 min). That is why the plan keeps a warm standby cluster, with the same manifests applied from Git and scaled to zero.

## 2. Evidence: DR drill (`scripts/dr_drill.sh`, log in `results/dr_drill.log`)

Steps: write 1,000 rows → take backups → write 250 more rows → **disaster: `DROP TABLE registrations`** → restore.

| Strategy | Restore time (lab) | Rows recovered | Data lost |
|---|---|---|---|
| A) logical `pg_dump` / `pg_restore` | 0.06 s | 1,000 / 1,250 | 250 rows: everything written after the dump (RPO = time since the last dump) |
| B) `pg_basebackup` + WAL archive + PITR (`recovery_target_time`) | 0.52 s | **1,250 / 1,250** | 0 rows (target set 1 s before the DROP) |

Important: **a streaming replica would not have helped here.** `DROP TABLE` is replicated to the standby within milliseconds. Only a backup with history (PITR) can return to the state before the error.

Lab times are small because the dataset is tiny. For the real 10,000-student registration database (a few GB), base-backup restore plus WAL replay is estimated at 3–8 min, which still fits the 15-minute budget.

## 3. Full-recovery runbook (outline)

1. **Detect and declare (≤ 2 min).** Alerts fire: probe failures, all endpoints NotReady, DB unreachable. The on-call engineer declares a disaster.
2. **Switch to the standby cluster (≤ 3 min).** Point kubectl or Argo CD at the DR cluster. Scale the deployments from 0 to 2.
3. **Apply configuration (≤ 2 min).** Run `kubectl apply -f k8s/` from Git, or sync with Argo CD. Restore secrets from the secret manager.
4. **Restore the database (≤ 5 min).** Restore the latest base backup and replay WAL to the latest point, or to a time just before the error. Check row counts and the latest registrations.
5. **Switch traffic (≤ 2 min).** Update the DNS or global load balancer record (TTL 60 s). Run smoke tests: `/healthz/ready`, one test registration.
6. **Post-incident review.** Confirm RPO and RTO against the targets, and update the runbook.

## 4. High availability vs disaster recovery

| | High availability (HA) | Disaster recovery (DR) |
|---|---|---|
| Question it answers | "How do we **keep running** when a component fails?" | "How do we **get back** after we have lost the system or its data?" |
| Typical failures | One pod, node, DB instance or zone | A whole cluster or region, data corruption, human error, ransomware |
| Mechanisms | Replicas, probes, load balancing, automatic failover, PDB | Backups with history, WAL archive / PITR, Git config, standby site, runbook, drills |
| Metric | Availability % (99.9%), detection and failover time in seconds | **RPO** (how much data we may lose) and **RTO** (how long recovery may take) |
| Timescale | Seconds, automatic | Minutes to hours, often with human decisions |
| Example from this project | Experiment 1: one replica was killed and users saw 0 errors (0 of 35 requests failed) | DR drill: `DROP TABLE` replicated instantly; only the PITR backup restored the data |

HA and DR are different engineering objectives because they cover different failure classes. **HA copies the current state; DR keeps the history of states.** HA redundancy faithfully reproduces logical errors, so a system can be 99.99% available and still lose all its data in one bad command. The reverse also holds: perfect backups do not prevent downtime, because restoring takes minutes. A resilient platform needs both. HA keeps the availability budget (≤ 8.76 h downtime per year), and DR bounds the worst case (≤ 5 min of lost data, ≤ 15 min to recover).

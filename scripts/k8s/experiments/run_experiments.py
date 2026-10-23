#!/usr/bin/env python3
"""Part D - all three failure experiments in a row on a REAL Kubernetes cluster.

Prerequisites: the stack is deployed (scripts/k8s/setup_kind.sh) and `kubectl`
points to that cluster. Works on macOS and Linux, Python 3.8+, standard library only.

  python3 scripts/k8s/experiments/run_experiments.py                 # experiments 1, 2, 3
  python3 scripts/k8s/experiments/run_experiments.py --db-outage 60  # DB down for 60 s

One experiment at a time:
  python3 scripts/k8s/experiments/one_replica.py      # 1 one Registration replica fails
  python3 scripts/k8s/experiments/all_replicas.py     # 2 all Registration replicas fail
  python3 scripts/k8s/experiments/database_down.py    # 3 database unavailable
  python3 scripts/k8s/experiments/liveness_hang.py    # 4 hung process, liveness restart

Modules
  cluster.py  kubectl wrapper, timeline log, ready-endpoint polling
  faults.py   the three experiments (inject fault, measure detection / recovery)
  load.py     load generator and user impact
  report.py   results/k8s/<timestamp>/ (results.md, results.json, load log, events, pods, timeline)
"""
import argparse
import os
from datetime import datetime, timezone

import faults
import load
import report
from cluster import ROOT, current_context, log


def main(experiments=(1, 2, 3), description=__doc__):
    """Run the given experiments under load and save the results."""
    ap = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    if 3 in experiments:
        ap.add_argument("--db-outage", type=int, default=60, help="seconds the DB stays down (exp 3)")
    db_outage = getattr(ap.parse_args(), "db_outage", 60)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = os.path.join(ROOT, "results", "k8s", stamp)
    ctx = current_context()
    log(f"kubectl context: {ctx}")

    since = load.start()
    results = [faults.run(n, db_outage) for n in experiments]

    reqs = load.samples(since)
    for r in results:
        r.update(load.impact(reqs, r["t0"], r["t_end"]))

    md = report.save(outdir, stamp, ctx, results, load.raw_log(since))
    load.stop()
    print("\n" + md + f"\nSaved to {os.path.relpath(outdir, ROOT)}/")


if __name__ == "__main__":
    main()

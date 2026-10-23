#!/usr/bin/env python3
"""Part D - failure experiments on a REAL Kubernetes cluster (kind / minikube / Docker Desktop).

Prerequisites: the stack is deployed (scripts/k8s/setup_kind.sh) and `kubectl`
points to that cluster. Works on macOS and Linux, Python 3.8+, standard library only.

  python3 scripts/k8s/experiments/run_experiments.py                 # experiments 1, 2, 3
  python3 scripts/k8s/experiments/run_experiments.py --only 1        # one experiment
  python3 scripts/k8s/experiments/run_experiments.py --db-outage 60  # DB down for 60 s

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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", type=int, default=[1, 2, 3], choices=[1, 2, 3])
    ap.add_argument("--db-outage", type=int, default=60, help="seconds the DB stays down (exp 3)")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = os.path.join(ROOT, "results", "k8s", stamp)
    ctx = current_context()
    log(f"kubectl context: {ctx}")

    since = load.start()
    results = [faults.run(n, args.db_outage) for n in args.only]

    reqs = load.samples(since)
    for r in results:
        r.update(load.impact(reqs, r["t0"], r["t_end"]))

    md = report.save(outdir, stamp, ctx, results, load.raw_log(since))
    load.stop()
    print("\n" + md + f"\nSaved to {os.path.relpath(outdir, ROOT)}/")


if __name__ == "__main__":
    main()

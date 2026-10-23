#!/usr/bin/env python3
"""Experiment 3 - database unavailable.

  python3 scripts/k8s/experiments/database_down.py                 # DB down for 60 s
  python3 scripts/k8s/experiments/database_down.py --db-outage 30  # DB down for 30 s

PostgreSQL is scaled to 0, then back to 1. Pods answer 503, readiness takes
them out of the Service (~10 s), and they come back on their own after the DB
returns. Expected: unavailable during the outage, 0 pod restarts.
"""
import argparse
import time

from helpers import kubectl, log, run_experiment

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--db-outage", type=int, default=60, help="seconds the DB stays down")
OUTAGE_S = ap.parse_args().db_outage


def inject(pods):
    log("FAULT: scale statefulset/postgres to 0")
    kubectl("scale", "statefulset/postgres", "--replicas=0")


def heal(t0):
    """Keep the DB down for the full outage, then bring it back."""
    time.sleep(max(0, OUTAGE_S - (time.time() - t0)))
    log("FAULT REMOVED: scale statefulset/postgres to 1")
    restored = round(time.time() - t0, 2)
    kubectl("scale", "statefulset/postgres", "--replicas=1")
    return {"db_restored_at_s": restored}


run_experiment(3, f"Database unavailable ({OUTAGE_S} s)", inject,
               detected=lambda ready: ready == 0, timeout=120, heal=heal)

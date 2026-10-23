#!/usr/bin/env python3
"""Experiment 3 - database unavailable.

  python3 scripts/k8s/experiments/database_down.py                 # DB down for 60 s
  python3 scripts/k8s/experiments/database_down.py --db-outage 30  # DB down for 30 s

PostgreSQL is scaled to 0, then back to 1. Pods answer 503, readiness takes
them out of the Service (~10 s), and they come back on their own after the DB
returns. Expected: unavailable during the outage, 0 pod restarts.
"""
from run_experiments import main

main([3], __doc__)

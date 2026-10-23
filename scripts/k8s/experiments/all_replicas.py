#!/usr/bin/env python3
"""Experiment 2 - all Registration replicas fail.

  python3 scripts/k8s/experiments/all_replicas.py

Both Registration pods are force-deleted at once. Nobody answers until the
ReplicaSet's new pods pass readiness. Expected: a short full outage (~1-2 s).
"""
from helpers import kill_pods, run_experiment


def inject(pods):
    kill_pods(pods)  # delete both pods


run_experiment(2, "All Registration replicas fail", inject,
               detected=lambda ready: ready == 0, timeout=60)

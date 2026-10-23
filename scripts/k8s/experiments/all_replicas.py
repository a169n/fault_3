#!/usr/bin/env python3
"""Experiment 2 - all Registration replicas fail.

  python3 scripts/k8s/experiments/all_replicas.py

Both Registration pods are force-deleted at once. Nobody answers until the
ReplicaSet's new pods pass readiness. Expected: a short full outage (~1-2 s).
"""
from run_experiments import main

main([2], __doc__)

#!/usr/bin/env python3
"""Experiment 1 - one Registration replica fails.

  python3 scripts/k8s/experiments/one_replica.py

One of the two Registration pods is force-deleted. The other replica keeps
serving; the ReplicaSet creates a replacement. Expected: no failed requests.
"""
from helpers import kill_pods, run_experiment


def inject(pods):
    kill_pods(pods[:1])  # delete one pod


run_experiment(1, "One Registration replica fails", inject,
               detected=lambda ready: ready < 2, timeout=60)

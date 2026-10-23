#!/usr/bin/env python3
"""Experiment 1 - one Registration replica fails.

  python3 scripts/k8s/experiments/one_replica.py

One of the two Registration pods is force-deleted. The other replica keeps
serving; the ReplicaSet creates a replacement. Expected: no failed requests.
"""
from run_experiments import main

main([1], __doc__)

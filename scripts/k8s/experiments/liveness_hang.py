#!/usr/bin/env python3
"""Experiment 4 (bonus) - a hung process is restarted by the liveness probe.

  python3 scripts/k8s/experiments/liveness_hang.py

POST /chaos/hang (enabled by CHAOS_ENABLED=true in the ConfigMap) makes
/healthz/live stop answering while the process stays alive. After 3 failed
liveness checks (~30 s) the kubelet restarts the container: RESTARTS +1, then
the pod is Ready again. The other replica keeps serving meanwhile.
Output: results/k8s/<timestamp>/liveness_hang.txt
"""
import os
import time

from helpers import ROOT, TIMELINE, kubectl, log, registration_pods, results_dir, write

HANG = ("import urllib.request as u; "
        "u.urlopen(u.Request('http://localhost:8080/chaos/hang', method='POST'))")
TIMEOUT_S = 120


def restarts_and_ready(pod):
    out = kubectl("get", "pod", pod, "-o", "jsonpath={.status.containerStatuses[0].restartCount} "
                  "{.status.containerStatuses[0].ready}", check=False).split()
    return (int(out[0]), out[1] == "true") if len(out) == 2 else (-1, False)


pod = registration_pods()[0]
start_restarts, _ = restarts_and_ready(pod)
log(f"FAULT: POST /chaos/hang on {pod} (restarts before: {start_restarts})")
kubectl("exec", pod, "--", "python", "-c", HANG)

t0, restarted = time.time(), None
while time.time() - t0 < TIMEOUT_S:
    restarts, ready = restarts_and_ready(pod)
    log(f"t+{time.time() - t0:.0f}s restarts={restarts} ready={ready}")
    if restarts > start_restarts and restarted is None:
        restarted = time.time() - t0
        log(f"DETECTED: liveness failed, container restarted after ~{restarted:.0f} s")
    if restarted is not None and ready:
        log(f"RECOVERED: pod Ready again after ~{time.time() - t0:.0f} s")
        break
    time.sleep(3)
else:
    log(f"TIMEOUT: no restart within {TIMEOUT_S} s")

events = kubectl("get", "events", "--field-selector", f"involvedObject.name={pod}",
                 "--sort-by=.lastTimestamp", check=False)
lines = [m for _, m in TIMELINE] + [""] + [e for e in events.splitlines()
                                          if any(k in e for k in ("Liveness", "Killing", "Started"))]
_, outdir = results_dir()
write(os.path.join(outdir, "liveness_hang.txt"), "\n".join(lines) + "\n")
print(f"\nSaved to {os.path.relpath(outdir, ROOT)}/liveness_hang.txt")

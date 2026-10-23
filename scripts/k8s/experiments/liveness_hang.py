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
from datetime import datetime, timezone

from cluster import ROOT, kubectl, log, registration_pods

HANG = ("import urllib.request as u; "
        "u.urlopen(u.Request('http://localhost:8080/chaos/hang', method='POST'))")
TIMEOUT_S = 120


def restarts_and_ready(pod):
    out = kubectl("get", "pod", pod, "-o", "jsonpath={.status.containerStatuses[0].restartCount} "
                  "{.status.containerStatuses[0].ready}", check=False).split()
    return (int(out[0]), out[1] == "true") if len(out) == 2 else (-1, False)


def main():
    pod = registration_pods()[0]
    lines = []

    def note(msg):
        log(msg)
        lines.append(msg)

    start_restarts, _ = restarts_and_ready(pod)
    note(f"FAULT: POST /chaos/hang on {pod} (restarts before: {start_restarts})")
    kubectl("exec", pod, "--", "python", "-c", HANG)

    t0, restarted = time.time(), None
    while time.time() - t0 < TIMEOUT_S:
        restarts, ready = restarts_and_ready(pod)
        note(f"t+{time.time() - t0:.0f}s restarts={restarts} ready={ready}")
        if restarts > start_restarts and restarted is None:
            restarted = time.time() - t0
            note(f"DETECTED: liveness failed, container restarted after ~{restarted:.0f} s")
        if restarted is not None and ready:
            note(f"RECOVERED: pod Ready again after ~{time.time() - t0:.0f} s")
            break
        time.sleep(3)
    else:
        note(f"TIMEOUT: no restart within {TIMEOUT_S} s")

    events = kubectl("get", "events", "--field-selector", f"involvedObject.name={pod}",
                     "--sort-by=.lastTimestamp", check=False)
    lines += ["", *[e for e in events.splitlines() if any(k in e for k in ("Liveness", "Killing", "Started"))]]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = os.path.join(ROOT, "results", "k8s", stamp)
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "liveness_hang.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nSaved to {os.path.relpath(outdir, ROOT)}/liveness_hang.txt")


if __name__ == "__main__":
    main()

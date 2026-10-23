#!/usr/bin/env python3
"""Part D - failure experiments on a REAL Kubernetes cluster (kind / minikube / Docker Desktop).

Prerequisites: the stack is deployed (scripts/k8s/setup_kind.sh) and `kubectl`
points to that cluster. Works on macOS and Linux, Python 3.8+, standard library only.

  python3 scripts/k8s/run_experiments.py                 # experiments 1, 2, 3
  python3 scripts/k8s/run_experiments.py --only 1        # one experiment
  python3 scripts/k8s/run_experiments.py --db-outage 60  # DB down for 60 s

Experiments
  1  one Registration replica fails     kubectl delete pod <one> --grace-period=0 --force
  2  all Registration replicas fail     kubectl delete pod <both> --grace-period=0 --force
  3  database unavailable               kubectl scale statefulset/postgres --replicas=0

Measurement
  * an in-cluster load generator (k8s/tools/load-generator.yaml) calls the Service
    5 times per second; its log (with kubectl timestamps) gives the user impact
  * the script polls EndpointSlices of `registration-service` every ~0.3 s:
      detection = fault injected -> ready endpoints drop below 2 (or to 0 for exp 2/3)
      recovery  = fault injected -> 2 ready endpoints again
Outputs: results/k8s/<timestamp>/ (results.md, results.json, load log, events, pods)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

NS = "aitu-sss"
SVC = "registration-service"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOADGEN = os.path.join(ROOT, "k8s", "tools", "load-generator.yaml")
TIMELINE = []


def kubectl(*args, check=True, capture=True):
    cmd = ["kubectl", "-n", NS, *args]
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def log(msg):
    ts = datetime.now(timezone.utc)
    TIMELINE.append((ts.isoformat(timespec="milliseconds"), msg))
    print(f"[{ts.strftime('%H:%M:%S.%f')[:-3]} UTC] {msg}", flush=True)


def ready_endpoints():
    out = kubectl("get", "endpointslices", "-l", f"kubernetes.io/service-name={SVC}", "-o", "json",
                  check=False)
    try:
        items = json.loads(out)["items"]
    except (ValueError, KeyError):
        return -1
    n = 0
    for s in items:
        for ep in s.get("endpoints") or []:
            if (ep.get("conditions") or {}).get("ready"):
                n += 1
    return n


def registration_pods():
    out = kubectl("get", "pods", "-l", f"app={SVC}", "-o", "json")
    return [p["metadata"]["name"] for p in json.loads(out)["items"]
            if not p["metadata"].get("deletionTimestamp")]


def wait_for(pred, timeout, what):
    start = time.time()
    while time.time() - start < timeout:
        n = ready_endpoints()
        if pred(n):
            return time.time(), n
        time.sleep(0.3)
    log(f"TIMEOUT waiting for: {what}")
    return None, ready_endpoints()


def parse_ts(s):
    # RFC3339Nano, e.g. 2026-10-12T10:00:00.123456789Z (fromisoformat cannot parse ns on py<3.11)
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", s)
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    frac = float("0." + (m.group(2) or "0"))
    return base.timestamp() + frac


def load_samples(since):
    out = kubectl("logs", "load-generator", "--timestamps", f"--since-time={since}", check=False)
    samples = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            samples.append((parse_ts(parts[0]), int(parts[1])))
    return samples


def impact(samples, t0, t1):
    win = [s for s in samples if t0 <= s[0] <= t1]
    bad = [s for s in win if not (200 <= s[1] < 300)]
    codes = {}
    for _, c in bad:
        codes[str(c)] = codes.get(str(c), 0) + 1
    return {
        "requests": len(win), "failed": len(bad),
        "failed_pct": round(100 * len(bad) / len(win), 1) if win else 0.0,
        "codes": codes,
        "visible_outage_s": round(bad[-1][0] - bad[0][0], 2) if bad else 0.0,
    }


def experiment(n, args):
    pods = registration_pods()
    _, cur = wait_for(lambda k: k == 2, 120, "2 ready endpoints before experiment")
    log(f"EXPERIMENT {n} begin, pods={pods}, ready endpoints={cur}")
    time.sleep(5)  # steady-state baseline
    res = {"experiment": n}
    t0 = time.time()
    if n == 1:
        res["name"] = "One Registration replica fails"
        log(f"FAULT: force delete {pods[0]}")
        kubectl("delete", "pod", pods[0], "--grace-period=0", "--force", "--wait=false", check=False)
        t_det, _ = wait_for(lambda k: k < 2, 60, "endpoint removed")
    elif n == 2:
        res["name"] = "All Registration replicas fail"
        log(f"FAULT: force delete {pods}")
        kubectl("delete", "pod", *pods, "--grace-period=0", "--force", "--wait=false", check=False)
        t_det, _ = wait_for(lambda k: k == 0, 60, "all endpoints removed")
    else:
        res["name"] = f"Database unavailable ({args.db_outage} s)"
        log("FAULT: scale statefulset/postgres to 0")
        kubectl("scale", "statefulset/postgres", "--replicas=0")
        t_det, _ = wait_for(lambda k: k == 0, 120, "readiness removes all endpoints")
    if t_det:
        log(f"DETECTED after {t_det - t0:.2f} s")
    if n == 3:
        remaining = args.db_outage - (time.time() - t0)
        if remaining > 0:
            time.sleep(remaining)
        log("FAULT REMOVED: scale statefulset/postgres to 1")
        t_db = time.time()
        kubectl("scale", "statefulset/postgres", "--replicas=1")
        res["db_restored_at_s"] = round(t_db - t0, 2)
    t_rec, _ = wait_for(lambda k: k == 2, 300, "2 ready endpoints again")
    if t_rec:
        log(f"RECOVERED (2 ready endpoints) after {t_rec - t0:.2f} s")
    time.sleep(5)
    res.update(t0=t0, t_end=time.time(),
               detection_time_s=round(t_det - t0, 2) if t_det else None,
               recovery_time_s=round(t_rec - t0, 2) if t_rec else None)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", type=int, default=[1, 2, 3], choices=[1, 2, 3])
    ap.add_argument("--db-outage", type=int, default=60, help="seconds the DB stays down (exp 3)")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = os.path.join(ROOT, "results", "k8s", stamp)
    os.makedirs(outdir, exist_ok=True)

    ctx = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True).stdout.strip()
    log(f"kubectl context: {ctx}")
    kubectl("rollout", "status", f"deployment/{SVC}", "--timeout=120s")
    kubectl("delete", "pod", "load-generator", "--ignore-not-found", "--wait=true")
    subprocess.run(["kubectl", "apply", "-f", LOADGEN], check=True, capture_output=True)
    kubectl("wait", "--for=condition=Ready", "pod/load-generator", "--timeout=120s")
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log("load generator running (5 req/s through the Service)")

    results = []
    for n in args.only:
        results.append(experiment(n, args))

    samples = load_samples(since)
    for r in results:
        r.update(impact(samples, r["t0"], r["t_end"]))

    with open(os.path.join(outdir, "load-generator.log"), "w") as f:
        f.write(kubectl("logs", "load-generator", "--timestamps", f"--since-time={since}", check=False))
    with open(os.path.join(outdir, "events.txt"), "w") as f:
        f.write(kubectl("get", "events", "--sort-by=.lastTimestamp", check=False))
    with open(os.path.join(outdir, "pods.txt"), "w") as f:
        f.write(kubectl("get", "pods", "-o", "wide", check=False))
    with open(os.path.join(outdir, "timeline.txt"), "w") as f:
        f.writelines(f"{t} {m}\n" for t, m in TIMELINE)
    with open(os.path.join(outdir, "results.json"), "w") as f:
        json.dump({"context": ctx, "results": results}, f, indent=2)

    lines = [f"# Part D - Kubernetes failure experiments ({stamp}, context `{ctx}`)\n",
             "| # | Injected failure | Detection time | Recovery time | Failed / total requests | Visible outage |",
             "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['experiment']} | {r['name']} | {r['detection_time_s']} s | {r['recovery_time_s']} s | "
                     f"{r['failed']} / {r['requests']} ({r['failed_pct']}%) {r['codes']} | {r['visible_outage_s']} s |")
    lines.append("\nHTTP code 000 = connection failed / timed out (no endpoint answered).")
    md = "\n".join(lines) + "\n"
    with open(os.path.join(outdir, "results.md"), "w") as f:
        f.write(md)
    kubectl("delete", "pod", "load-generator", "--wait=false", check=False)
    print("\n" + md + f"\nSaved to {os.path.relpath(outdir, ROOT)}/")


if __name__ == "__main__":
    main()

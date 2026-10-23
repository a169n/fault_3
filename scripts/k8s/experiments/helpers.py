"""Shared code for the Part D experiments: kubectl, polling, load generator, results.

Every experiment file calls run_experiment(): start the load generator, wait for
2 ready pods, inject the fault, measure detection and recovery from the Service
EndpointSlices, count failed user requests, save results/k8s/<timestamp>/.

  detection = fault injected -> ready endpoints drop
  recovery  = fault injected -> 2 ready endpoints again
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))  # repository root
NS = "aitu-sss"
SVC = "registration-service"
LOAD_POD = "load-generator"
LOAD_MANIFEST = os.path.join(ROOT, "k8s", "tools", "load-generator.yaml")
BASELINE_S = 5  # steady traffic before and after each fault
TIMELINE = []   # (iso time, message) - saved as timeline.txt


# ---------------------------------------------------------------- cluster
def kubectl(*args, check=True):
    """Run `kubectl -n aitu-sss ...` and return stdout; exit on error if check is set."""
    cmd = ["kubectl", "-n", NS, *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def log(msg):
    """Print a UTC-stamped line and remember it for timeline.txt."""
    ts = datetime.now(timezone.utc)
    TIMELINE.append((ts.isoformat(timespec="milliseconds"), msg))
    print(f"[{ts.strftime('%H:%M:%S.%f')[:-3]} UTC] {msg}", flush=True)


def registration_pods():
    """Names of the live Registration pods (found by label, not by name)."""
    out = kubectl("get", "pods", "-l", f"app={SVC}", "-o", "json")
    return [p["metadata"]["name"] for p in json.loads(out)["items"]
            if not p["metadata"].get("deletionTimestamp")]


def kill_pods(pods):
    log(f"FAULT: force delete {pods if len(pods) > 1 else pods[0]}")
    kubectl("delete", "pod", *pods, "--grace-period=0", "--force", "--wait=false", check=False)


def ready_endpoints():
    """How many Registration pods the Service currently sends traffic to (-1 = unknown)."""
    out = kubectl("get", "endpointslices", "-l", f"kubernetes.io/service-name={SVC}",
                  "-o", "json", check=False)
    try:
        slices = json.loads(out)["items"]
    except (ValueError, KeyError):
        return -1
    return sum(1 for s in slices for ep in s.get("endpoints") or []
               if (ep.get("conditions") or {}).get("ready"))


def wait_for(condition, timeout, what):
    """Poll ready endpoints every 0.3 s until condition(count) holds; return the time or None."""
    start = time.time()
    while time.time() - start < timeout:
        if condition(ready_endpoints()):
            return time.time()
        time.sleep(0.3)
    log(f"TIMEOUT waiting for: {what}")
    return None


# ---------------------------------------------------------------- load generator / user impact
def start_load():
    """(Re)start the in-cluster load generator (5 req/s); return the time its log starts from."""
    kubectl("rollout", "status", f"deployment/{SVC}", "--timeout=120s")
    kubectl("delete", "pod", LOAD_POD, "--ignore-not-found", "--wait=true")
    subprocess.run(["kubectl", "apply", "-f", LOAD_MANIFEST], check=True, capture_output=True)
    kubectl("wait", "--for=condition=Ready", f"pod/{LOAD_POD}", "--timeout=120s")
    log("load generator running (5 req/s through the Service)")
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_log(since):
    return kubectl("logs", LOAD_POD, "--timestamps", f"--since-time={since}", check=False)


def parse_ts(s):
    # RFC3339Nano, e.g. 2026-10-12T10:00:00.123456789Z (fromisoformat cannot parse ns on py<3.11)
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", s)
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return base.timestamp() + float("0." + (m.group(2) or "0"))


def user_impact(log_text, t0, t1):
    """Failed requests and visible outage inside [t0, t1], from the load generator log."""
    reqs = [(parse_ts(p[0]), int(p[1])) for p in (line.split() for line in log_text.splitlines())
            if len(p) == 2 and p[1].isdigit()]
    window = [r for r in reqs if t0 <= r[0] <= t1]
    failed = [r for r in window if not 200 <= r[1] < 300]
    codes = {}
    for _, code in failed:
        codes[str(code)] = codes.get(str(code), 0) + 1
    return {
        "requests": len(window), "failed": len(failed),
        "failed_pct": round(100 * len(failed) / len(window), 1) if window else 0.0,
        "codes": codes,
        "visible_outage_s": round(failed[-1][0] - failed[0][0], 2) if failed else 0.0,
    }


# ---------------------------------------------------------------- results
def results_dir():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(ROOT, "results", "k8s", stamp)
    os.makedirs(path, exist_ok=True)
    return stamp, path


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def save_results(result, load_text):
    """Write results.md/.json, timeline, load log, events and pods; print the table."""
    stamp, outdir = results_dir()
    ctx = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True).stdout.strip()
    r = result
    md = (f"# Part D - Kubernetes failure experiment ({stamp}, context `{ctx}`)\n\n"
          "| # | Injected failure | Detection time | Recovery time | Failed / total requests | Visible outage |\n"
          "|---|---|---|---|---|---|\n"
          f"| {r['experiment']} | {r['name']} | {r['detection_time_s']} s | {r['recovery_time_s']} s | "
          f"{r['failed']} / {r['requests']} ({r['failed_pct']}%) {r['codes']} | {r['visible_outage_s']} s |\n"
          "\nHTTP code 000 = connection failed / timed out (no endpoint answered).\n")
    write(os.path.join(outdir, "results.md"), md)
    write(os.path.join(outdir, "results.json"), json.dumps({"context": ctx, "results": [r]}, indent=2))
    write(os.path.join(outdir, "timeline.txt"), "".join(f"{t} {m}\n" for t, m in TIMELINE))
    write(os.path.join(outdir, "load-generator.log"), load_text)
    write(os.path.join(outdir, "events.txt"), kubectl("get", "events", "--sort-by=.lastTimestamp", check=False))
    write(os.path.join(outdir, "pods.txt"), kubectl("get", "pods", "-o", "wide", check=False))
    print("\n" + md + f"\nSaved to {os.path.relpath(outdir, ROOT)}/")


# ---------------------------------------------------------------- one experiment, end to end
def run_experiment(number, name, inject, detected, timeout, heal=None):
    """inject(pods) breaks something; detected(ready_count) says the break is visible;
    heal(t0) optionally repairs it and returns extra result fields."""
    since = start_load()
    if wait_for(lambda n: n == 2, 120, "2 ready endpoints before experiment") is None:
        sys.exit("Registration is not healthy (need 2 ready pods) - check `kubectl -n aitu-sss get pods`")
    pods = registration_pods()
    log(f"EXPERIMENT {number} begin, pods={pods}, ready endpoints=2")
    time.sleep(BASELINE_S)

    t0 = time.time()
    inject(pods)
    t_det = wait_for(detected, timeout, "fault visible in the Service endpoints")
    if t_det:
        log(f"DETECTED after {t_det - t0:.2f} s")

    result = {"experiment": number, "name": name}
    if heal:
        result.update(heal(t0))
    t_rec = wait_for(lambda n: n == 2, 300, "2 ready endpoints again")
    if t_rec:
        log(f"RECOVERED (2 ready endpoints) after {t_rec - t0:.2f} s")
    time.sleep(BASELINE_S)

    result.update(detection_time_s=round(t_det - t0, 2) if t_det else None,
                  recovery_time_s=round(t_rec - t0, 2) if t_rec else None)
    text = load_log(since)
    result.update(user_impact(text, t0, time.time()))
    save_results(result, text)
    kubectl("delete", "pod", LOAD_POD, "--wait=false", check=False)

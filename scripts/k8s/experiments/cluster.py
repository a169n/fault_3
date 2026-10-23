"""Talking to the cluster: kubectl wrapper, timeline log, ready-endpoint polling."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))  # repository root
NS = "aitu-sss"
SVC = "registration-service"
TIMELINE = []  # (iso time, message) - saved as timeline.txt


def kubectl(*args, check=True):
    """Run `kubectl -n aitu-sss ...` and return stdout; exit on error if check is set."""
    cmd = ["kubectl", "-n", NS, *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr}")
    return r.stdout


def current_context():
    return subprocess.run(["kubectl", "config", "current-context"],
                          capture_output=True, text=True).stdout.strip()


def log(msg):
    """Print a UTC-stamped line and remember it for timeline.txt."""
    ts = datetime.now(timezone.utc)
    TIMELINE.append((ts.isoformat(timespec="milliseconds"), msg))
    print(f"[{ts.strftime('%H:%M:%S.%f')[:-3]} UTC] {msg}", flush=True)


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


def registration_pods():
    """Names of the live Registration pods (found by label, not by name)."""
    out = kubectl("get", "pods", "-l", f"app={SVC}", "-o", "json")
    return [p["metadata"]["name"] for p in json.loads(out)["items"]
            if not p["metadata"].get("deletionTimestamp")]


def wait_for(condition, timeout, what):
    """Poll ready endpoints every 0.3 s until condition(count) holds.

    Returns (time it happened or None on timeout, last count)."""
    start = time.time()
    while time.time() - start < timeout:
        n = ready_endpoints()
        if condition(n):
            return time.time(), n
        time.sleep(0.3)
    log(f"TIMEOUT waiting for: {what}")
    return None, ready_endpoints()

"""User impact: the in-cluster load generator and what its log says.

k8s/tools/load-generator.yaml calls the Service 5 times per second and prints
the HTTP code of every request (000 = no endpoint answered).
"""
import os
import re
import subprocess
from datetime import datetime, timezone

from cluster import ROOT, SVC, kubectl, log

POD = "load-generator"
MANIFEST = os.path.join(ROOT, "k8s", "tools", "load-generator.yaml")


def start():
    """(Re)start the load generator; return the time its log starts from."""
    kubectl("rollout", "status", f"deployment/{SVC}", "--timeout=120s")
    kubectl("delete", "pod", POD, "--ignore-not-found", "--wait=true")
    subprocess.run(["kubectl", "apply", "-f", MANIFEST], check=True, capture_output=True)
    kubectl("wait", "--for=condition=Ready", f"pod/{POD}", "--timeout=120s")
    log("load generator running (5 req/s through the Service)")
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stop():
    kubectl("delete", "pod", POD, "--wait=false", check=False)


def raw_log(since):
    return kubectl("logs", POD, "--timestamps", f"--since-time={since}", check=False)


def parse_ts(s):
    # RFC3339Nano, e.g. 2026-10-12T10:00:00.123456789Z (fromisoformat cannot parse ns on py<3.11)
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", s)
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return base.timestamp() + float("0." + (m.group(2) or "0"))


def samples(since):
    """[(unix time, http code), ...] for every request since `since`."""
    out = []
    for line in raw_log(since).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out.append((parse_ts(parts[0]), int(parts[1])))
    return out


def impact(reqs, t0, t1):
    """Failed requests and visible outage inside the window [t0, t1]."""
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

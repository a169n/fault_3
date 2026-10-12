#!/usr/bin/env python3
"""Part D - local failure simulation (no Kubernetes cluster required).

What it does
------------
Runs the *real* Registration Service (app/registration_service.py) as 2 replica
processes and emulates the Kubernetes pieces that matter for resilience:

  * kubelet     - startup / readiness / liveness probes with the SAME parameters as
                  k8s/11-registration-deployment.yaml (parsed from the file),
                  container-exit detection (PLEG, 1 s) and automatic restart
                  (restartPolicy: Always, CrashLoopBackOff 0 s, 10 s, 20 s ...)
  * Service     - round-robin load balancing over READY endpoints only
                  (503 "no healthy upstream" when the endpoint list is empty)
  * Database    - a TCP listener that can be stopped / started (DB outage)
  * Client      - a load generator sending 20 req/s through the Service

Experiments (assignment Part D + one bonus):
  1  one Registration replica crashes          (SIGKILL one process)
  2  all Registration replicas crash           (SIGKILL both processes)
  3  database unavailable for DB_OUTAGE_S s     (stop the DB listener)
  4  bonus: one replica hangs -> liveness restart

Usage (macOS / Linux, Python 3.9+, only the standard library; PyYAML optional):
  python3 scripts/simulate_failures.py               # all experiments, ~3 min
  python3 scripts/simulate_failures.py --only 1 3    # selected experiments

Outputs in results/:
  simulation_events.log      timeline of orchestrator events
  simulation_requests.csv    every client request (time, experiment, status, replica)
  simulation_results.json    measured numbers
  simulation_results.md      table for the report
  pod-logs/*.log             stdout of every replica process
"""
import argparse
import csv
import http.client
import json
import os
import random
import re
import signal
import socket
import string
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app", "registration_service.py")
DEPLOYMENT = os.path.join(ROOT, "k8s", "11-registration-deployment.yaml")
RESULTS = os.path.join(ROOT, "results")

PLEG_PERIOD = 1.0          # kubelet notices container exit within ~1 s
CLIENT_INTERVAL = 0.05     # 20 requests / second
DB_OUTAGE_S = 30

T0 = time.monotonic()
LOCK = threading.Lock()
EVENTS = []                # (t, experiment, kind, message)
REQUESTS = []              # (t, experiment, status, served_by)
CURRENT_EXP = {"id": "warmup"}


def now():
    return time.monotonic() - T0


def wall():
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def event(kind, msg):
    t = now()
    with LOCK:
        EVENTS.append((t, CURRENT_EXP["id"], kind, msg))
        print(f"[{wall()} | t={t:7.2f}s | exp {CURRENT_EXP['id']}] {kind:<18} {msg}", flush=True)


# ------------------------------------------------------------------ probe config
DEFAULT_PROBES = {
    "startupProbe": {"periodSeconds": 2, "failureThreshold": 12, "timeoutSeconds": 1},
    "readinessProbe": {"periodSeconds": 5, "failureThreshold": 2, "timeoutSeconds": 2,
                       "successThreshold": 1},
    "livenessProbe": {"periodSeconds": 10, "failureThreshold": 3, "timeoutSeconds": 2},
}


def load_probe_config():
    """Read probe settings from the Deployment manifest so that the simulation
    always uses exactly the values that would be deployed."""
    try:
        import yaml  # optional
        with open(DEPLOYMENT) as f:
            doc = yaml.safe_load(f)
        c = doc["spec"]["template"]["spec"]["containers"][0]
        cfg = {}
        for name, default in DEFAULT_PROBES.items():
            p = dict(default)
            for k in ("periodSeconds", "failureThreshold", "timeoutSeconds",
                      "successThreshold", "initialDelaySeconds"):
                if k in c.get(name, {}):
                    p[k] = c[name][k]
            p["path"] = c[name]["httpGet"]["path"]
            cfg[name] = p
        grace = doc["spec"]["template"]["spec"].get("terminationGracePeriodSeconds", 30)
        return cfg, grace, "parsed from k8s/11-registration-deployment.yaml"
    except Exception as exc:  # PyYAML missing or file changed
        cfg = {k: dict(v) for k, v in DEFAULT_PROBES.items()}
        cfg["startupProbe"]["path"] = "/healthz/live"
        cfg["readinessProbe"]["path"] = "/healthz/ready"
        cfg["livenessProbe"]["path"] = "/healthz/live"
        return cfg, 30, f"defaults (could not parse manifest: {exc.__class__.__name__})"


# ------------------------------------------------------------------ fake database
class FakeDatabase:
    """TCP listener standing in for PostgreSQL. stop() -> connection refused."""

    def __init__(self, port):
        self.port = port
        self.sock = None
        self.thread = None

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(128)
        self.sock = s
        self.thread = threading.Thread(target=self._serve, args=(s,), daemon=True)
        self.thread.start()

    def _serve(self, s):
        while True:
            try:
                conn, _ = s.accept()
                conn.close()
            except OSError:
                return

    def stop(self):
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.sock = None


# ------------------------------------------------------------------ pod + kubelet
def http_get(port, path, timeout):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        return conn.getresponse().status
    finally:
        conn.close()


class Pod:
    """One replica: a container process plus the kubelet logic around it."""

    def __init__(self, name, port, db_port, probes, grace, on_ready_change):
        self.name, self.port, self.db_port = name, port, db_port
        self.probes, self.grace = probes, grace
        self.on_ready_change = on_ready_change
        self.proc = None
        self.restart_count = 0
        self.backoff_level = 0       # CrashLoopBackOff level, reset after 10 min of healthy run
        self.restarting = False
        self.container_started_at = 0.0
        self.ready = False
        self.started = False
        self.gen = 0
        self.lock = threading.Lock()
        self.stop_flag = False
        self.logf = open(os.path.join(RESULTS, "pod-logs", f"{name}.log"), "a")
        self.thread = threading.Thread(target=self._kubelet_loop, daemon=True)

    # --- container lifecycle
    def start_container(self):
        env = dict(os.environ, PORT=str(self.port), POD_NAME=self.name,
                   DB_HOST="127.0.0.1", DB_PORT=str(self.db_port),
                   STORAGE_BACKEND="memory", CHAOS_ENABLED="true",
                   DB_CHECK_TIMEOUT_SECONDS="1", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen([sys.executable, APP], env=env,
                                     stdout=self.logf, stderr=subprocess.STDOUT)
        with self.lock:
            self.gen += 1
            self.container_started_at = now()
            self.restarting = False
            self.started = False
            self.fail = {"startupProbe": 0, "readinessProbe": 0, "livenessProbe": 0}
            self.succ = {"readinessProbe": 0}
            t = now()
            self.next_due = {"startupProbe": t, "readinessProbe": None, "livenessProbe": None}
            self.inflight = {k: False for k in self.next_due}
        self._set_ready(False, "container started")
        event("container_start", f"{self.name} pid={self.proc.pid} restarts={self.restart_count}")

    def _set_ready(self, value, reason):
        if self.ready != value:
            self.ready = value
            event("pod_ready" if value else "pod_not_ready", f"{self.name}: {reason}")
            self.on_ready_change()

    def kill(self, sig=signal.SIGKILL):
        """Fault injection: crash the container process."""
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(sig)

    def _restart(self, reason):
        # Called from the kubelet loop (container exited) or a probe thread
        # (liveness failed); only one restart may be in progress at a time.
        with self.lock:
            if self.restarting:
                return
            self.restarting = True
        self._set_ready(False, reason)
        if self.proc.poll() is None:  # graceful stop: SIGTERM, then SIGKILL after grace
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(self.grace)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        # CrashLoopBackOff: first restart immediately, then 10 s, 20 s, 40 s ... (max 300 s);
        # the back-off is reset once a container has run fine for 10 minutes.
        if now() - self.container_started_at > 600:
            self.backoff_level = 0
        backoff = 0 if self.backoff_level == 0 else min(10 * 2 ** (self.backoff_level - 1), 300)
        self.backoff_level += 1
        self.restart_count += 1
        if backoff:
            event("crash_backoff", f"{self.name}: waiting {backoff}s before restart")
            time.sleep(backoff)
        self.start_container()

    # --- probes
    def _run_probe(self, kind, gen):
        p = self.probes[kind]
        try:
            ok = http_get(self.port, p["path"], p["timeoutSeconds"]) == 200
        except Exception:
            ok = False
        with self.lock:
            self.inflight[kind] = False
            if gen != self.gen or self.restarting:
                return  # result from a previous container instance
        self._probe_result(kind, ok)

    def _probe_result(self, kind, ok):
        p = self.probes[kind]
        if kind == "startupProbe":
            if ok:
                self.started = True
                t = now()
                self.next_due.update(startupProbe=None, readinessProbe=t, livenessProbe=t + p["periodSeconds"])
                event("startup_ok", self.name)
            else:
                self.fail[kind] += 1
                if self.fail[kind] >= p["failureThreshold"]:
                    event("startup_failed", self.name)
                    self._restart("startup probe failed")
        elif kind == "readinessProbe":
            if ok:
                self.fail[kind] = 0
                self.succ[kind] += 1
                if self.succ[kind] >= p.get("successThreshold", 1):
                    self._set_ready(True, "readiness probe passed")
            else:
                self.succ[kind] = 0
                self.fail[kind] += 1
                event("readiness_fail", f"{self.name} consecutive={self.fail[kind]} "
                                        f"threshold={p['failureThreshold']}")
                if self.fail[kind] >= p["failureThreshold"]:
                    self._set_ready(False, "readiness probe failed -> removed from endpoints")
        elif kind == "livenessProbe":
            if ok:
                self.fail[kind] = 0
            else:
                self.fail[kind] += 1
                event("liveness_fail", f"{self.name} ({self.fail[kind]}/{p['failureThreshold']})")
                if self.fail[kind] >= p["failureThreshold"]:
                    event("liveness_restart", f"{self.name}: kubelet restarts container")
                    self._restart("liveness probe failed")

    def _kubelet_loop(self):
        next_pleg = now()
        while not self.stop_flag:
            t = now()
            if self.restarting:
                time.sleep(0.05)
                continue
            if t >= next_pleg:
                next_pleg = t + PLEG_PERIOD
                if self.proc.poll() is not None:
                    event("container_exited", f"{self.name} exit={self.proc.returncode}")
                    self._restart("container exited")
                    continue
            for kind, due in list(self.next_due.items()):
                if due is not None and t >= due and not self.inflight[kind]:
                    self.next_due[kind] = t + self.probes[kind]["periodSeconds"]
                    self.inflight[kind] = True
                    threading.Thread(target=self._run_probe, args=(kind, self.gen), daemon=True).start()
            time.sleep(0.05)

    def shutdown(self):
        self.stop_flag = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ------------------------------------------------------------------ Service (load balancer)
class ServiceProxy:
    def __init__(self, port, pods):
        self.port, self.pods = port, pods
        self.endpoints = []
        self.rr = 0
        self.lock = threading.Lock()

    def update_endpoints(self):
        with self.lock:
            new = [p for p in self.pods if p.ready]
            if [p.name for p in new] != [p.name for p in self.endpoints]:
                event("endpoints", f"{len(new)} ready: {[p.name for p in new]}")
            self.endpoints = new

    def pick(self):
        with self.lock:
            if not self.endpoints:
                return None
            self.rr = (self.rr + 1) % len(self.endpoints)
            return self.endpoints[self.rr]

    def start(self):
        proxy = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _reply(self, code, body, served_by="-"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Served-By", served_by)
                self.end_headers()
                self.wfile.write(data)

            def _forward(self, method):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                pod = proxy.pick()
                if pod is None:
                    return self._reply(503, {"error": "no healthy upstream"})
                try:
                    c = http.client.HTTPConnection("127.0.0.1", pod.port, timeout=2)
                    c.request(method, self.path, body=body,
                              headers={"Content-Type": "application/json"})
                    r = c.getresponse()
                    data = r.read()
                    c.close()
                    return self._reply(r.status, data, pod.name)
                except Exception:
                    # endpoint still listed but process is dead -> like a TCP reset in kube-proxy
                    return self._reply(502, {"error": "upstream connection failed"}, pod.name)

            def do_GET(self):
                self._forward("GET")

            def do_POST(self):
                self._forward("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


# ------------------------------------------------------------------ client
class LoadGenerator:
    def __init__(self, port):
        self.port = port
        self.running = True
        self.i = 0

    def run(self):
        while self.running:
            self.i += 1
            t = now()
            status, served = 0, "-"
            try:
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
                body = json.dumps({"student_id": f"S{self.i % 500:04d}", "course_code": "CC-301"})
                c.request("POST", "/api/v1/registrations", body=body,
                          headers={"Content-Type": "application/json"})
                r = c.getresponse()
                r.read()
                status, served = r.status, r.getheader("X-Served-By", "-")
                c.close()
            except Exception:
                status = 0  # client-side timeout / connection error
            with LOCK:
                REQUESTS.append((t, CURRENT_EXP["id"], status, served))
            time.sleep(max(0.0, CLIENT_INTERVAL - (now() - t)))


# ------------------------------------------------------------------ experiments
def wait_until(cond, timeout, step=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


def first_event_after(t0, kinds, exp):
    for t, e, k, _ in EVENTS:
        if e == exp and t >= t0 and k in kinds:
            return t
    return None


def analyse(exp, t0, t_end, t_fault_end=None, full_recovery=None):
    """Compute user impact for requests in [t0, t_end]."""
    reqs = [r for r in REQUESTS if r[1] == exp and t0 <= r[0] <= t_end]
    failed = [r for r in reqs if not (200 <= r[2] < 300)]
    codes = {}
    for r in failed:
        codes[str(r[2])] = codes.get(str(r[2]), 0) + 1
    outage = (failed[-1][0] - failed[0][0]) if failed else 0.0
    first_ok_after = None
    if failed:
        for r in reqs:
            if r[0] > failed[-1][0] and 200 <= r[2] < 300:
                first_ok_after = r[0] - t0
                break
    return {
        "requests_in_window": len(reqs),
        "failed_requests": len(failed),
        "failed_pct": round(100 * len(failed) / len(reqs), 1) if reqs else 0.0,
        "error_codes": codes,
        "user_visible_outage_s": round(outage, 2),
        "first_success_after_outage_s": round(first_ok_after, 2) if first_ok_after else None,
    }


def run_experiment(exp_id, pods, proxy, db, probes):
    CURRENT_EXP["id"] = str(exp_id)
    name = {1: "One Registration replica crashes (SIGKILL)",
            2: "All Registration replicas crash (SIGKILL x2)",
            3: f"Database unavailable for {DB_OUTAGE_S}s",
            4: "Bonus: one replica hangs (liveness failure)"}[exp_id]
    event("experiment_begin", name)
    time.sleep(3)  # steady-state baseline
    restarts_before = {p.name: p.restart_count for p in pods}
    t0 = now()
    res = {"experiment": exp_id, "name": name}

    if exp_id == 1:
        event("FAULT_INJECTED", f"kill -9 {pods[0].name}")
        pods[0].kill()
        wait_until(lambda: not pods[0].ready, 10)
        t_detect = first_event_after(t0, {"container_exited", "pod_not_ready"}, "1")
        wait_until(lambda: all(p.ready for p in pods), 60)
        t_recover = first_event_after(t0 + 0.01, {"pod_ready"}, "1")
    elif exp_id == 2:
        event("FAULT_INJECTED", "kill -9 " + " ".join(p.name for p in pods))
        for p in pods:
            p.kill()
        wait_until(lambda: not any(p.ready for p in pods), 10)
        t_detect = first_event_after(t0, {"container_exited", "pod_not_ready"}, "2")
        wait_until(lambda: all(p.ready for p in pods), 60)
        t_recover = now()
    elif exp_id == 3:
        event("FAULT_INJECTED", f"database stopped for {DB_OUTAGE_S}s")
        db.stop()
        wait_until(lambda: not any(p.ready for p in pods), 30)
        t_detect = first_event_after(t0, {"pod_not_ready"}, "3")
        t_all_removed = now()
        time.sleep(max(0, DB_OUTAGE_S - (now() - t0)))
        db.start()
        t_db_back = now()
        event("FAULT_REMOVED", "database started again")
        wait_until(lambda: all(p.ready for p in pods), 60)
        t_recover = now()
        res["all_endpoints_removed_s"] = round(t_all_removed - t0, 2)
        res["db_restored_at_s"] = round(t_db_back - t0, 2)
        res["recovery_after_db_restored_s"] = round(t_recover - t_db_back, 2)
    else:
        event("FAULT_INJECTED", f"POST /chaos/hang -> {pods[1].name}")
        http.client.HTTPConnection("127.0.0.1", pods[1].port, timeout=2).request("POST", "/chaos/hang")
        wait_until(lambda: pods[1].restart_count > restarts_before[pods[1].name], 90)
        t_detect = first_event_after(t0, {"liveness_restart"}, "4")
        wait_until(lambda: all(p.ready for p in pods), 60)
        t_recover = now()

    time.sleep(3)  # observe steady state after recovery
    t_end = now()
    res["detection_time_s"] = round(t_detect - t0, 2) if t_detect else None
    res["full_recovery_time_s"] = round(t_recover - t0, 2) if t_recover else None
    res["restarts"] = {p.name: p.restart_count - restarts_before[p.name] for p in pods}
    res.update(analyse(str(exp_id), t0, t_end))
    event("experiment_end", json.dumps({k: res[k] for k in
          ("detection_time_s", "full_recovery_time_s", "failed_requests", "requests_in_window")}))
    return res


def pod_name():
    rs = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"registration-service-{rs[:5]}-{rs[5:]}"


def write_outputs(results, probes, source):
    with open(os.path.join(RESULTS, "simulation_events.log"), "w") as f:
        for t, e, k, m in EVENTS:
            f.write(f"t={t:8.3f}s exp={e:<6} {k:<18} {m}\n")
    with open(os.path.join(RESULTS, "simulation_requests.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_seconds", "experiment", "http_status", "served_by"])
        for t, e, s, b in REQUESTS:
            w.writerow([f"{t:.3f}", e, s, b])
    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": sys.platform, "python": sys.version.split()[0],
        "probe_config_source": source, "probes": probes,
        "client_rate_rps": round(1 / CLIENT_INTERVAL), "pleg_period_s": PLEG_PERIOD,
    }
    with open(os.path.join(RESULTS, "simulation_results.json"), "w") as f:
        json.dump({"meta": meta, "experiments": results}, f, indent=2)

    impact = {
        1: "Partial: in-flight requests to the crashed replica fail until it is removed from endpoints; then the 2nd replica serves 100% of traffic",
        2: "Full outage of registration until a container restarts and passes readiness",
        3: "Registration unavailable (fast 503 + Retry-After, no hanging requests); no pod restarts",
        4: "None for users if readiness still passes; container restarted automatically",
    }
    lines = [
        "# Part D - Failure simulation results (generated by scripts/simulate_failures.py)\n",
        f"- Generated: {meta['generated_at_utc']} on `{meta['platform']}`, Python {meta['python']}",
        f"- Probe settings: {source}",
        f"- Client load: {meta['client_rate_rps']} req/s POST /api/v1/registrations through the Service proxy\n",
        "| # | Injected failure | Detection time | Recovery time | Failed / total requests | User impact |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        rec = f"{r['full_recovery_time_s']} s"
        if r["experiment"] == 3:
            rec += f" (DB down {DB_OUTAGE_S} s; ready {r['recovery_after_db_restored_s']} s after DB returned)"
        lines.append(
            f"| {r['experiment']} | {r['name']} | {r['detection_time_s']} s | {rec} | "
            f"{r['failed_requests']} / {r['requests_in_window']} ({r['failed_pct']}%), codes {r['error_codes']} | "
            f"{impact[r['experiment']]}; visible outage {r['user_visible_outage_s']} s |")
    lines += [
        "",
        "Definitions: *detection* = fault injected -> orchestrator marks the pod NotReady / "
        "restarts it; *recovery* = fault injected -> all replicas Ready again "
        "(full capacity); *visible outage* = first to last failed client request.",
        "",
        "Restarts per experiment: " + "; ".join(f"exp {r['experiment']}: {r['restarts']}" for r in results),
    ]
    with open(os.path.join(RESULTS, "simulation_results.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    global DB_OUTAGE_S
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", type=int, default=[1, 2, 3, 4], choices=[1, 2, 3, 4])
    ap.add_argument("--base-port", type=int, default=18080)
    ap.add_argument("--db-outage", type=int, default=DB_OUTAGE_S)
    args = ap.parse_args()
    DB_OUTAGE_S = args.db_outage

    os.makedirs(os.path.join(RESULTS, "pod-logs"), exist_ok=True)
    for f in os.listdir(os.path.join(RESULTS, "pod-logs")):
        os.remove(os.path.join(RESULTS, "pod-logs", f))
    for port in (args.base_port, args.base_port + 1, args.base_port + 2, args.base_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                sys.exit(f"port {port} is already in use (leftover replica process?). "
                         f"Stop it or run with --base-port <other port>.")
    probes, grace, source = load_probe_config()
    event("config", f"probes {source}: " + json.dumps(probes))

    db = FakeDatabase(args.base_port + 100)
    db.start()
    pods = []
    proxy = ServiceProxy(args.base_port, pods)
    for i in range(2):  # replicas: 2
        pods.append(Pod(pod_name(), args.base_port + 1 + i, db.port, probes, grace,
                        proxy.update_endpoints))
    proxy.start()
    for p in pods:
        p.start_container()
        p.thread.start()
    if not wait_until(lambda: all(p.ready for p in pods), 60):
        sys.exit("replicas did not become ready - see results/pod-logs/")

    client = LoadGenerator(args.base_port)
    threading.Thread(target=client.run, daemon=True).start()

    results = []
    try:
        for exp in args.only:
            results.append(run_experiment(exp, pods, proxy, db, probes))
    finally:
        client.running = False
        time.sleep(0.2)
        for p in pods:
            p.shutdown()
        db.stop()
        write_outputs(results, probes, source)
    print("\n" + open(os.path.join(RESULTS, "simulation_results.md")).read())


if __name__ == "__main__":
    main()

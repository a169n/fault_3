#!/usr/bin/env python3
"""Synthetic AITU Registration Service (educational, Assignment 3).

A deliberately small HTTP service used to demonstrate Kubernetes resilience
mechanisms. It has no third-party dependencies unless STORAGE_BACKEND=postgres
is selected (then the pure-Python driver `pg8000` is required).

Endpoints
---------
GET  /healthz/live                 liveness  - is the process able to serve at all?
GET  /healthz/ready                readiness - can this replica do useful work (DB reachable)?
GET  /api/v1/info                  which replica answered (used to observe load balancing)
POST /api/v1/registrations         {"student_id": "S0001", "course_code": "CC-301"}
GET  /api/v1/registrations?student_id=S0001
POST /chaos/hang                   (only if CHAOS_ENABLED=true) make liveness fail

Design decision: liveness does NOT depend on the database. If it did, a database
outage would make Kubernetes restart every healthy replica in a loop, which
adds load and hides the real root cause. Only readiness depends on the DB.

All data handled by this service is synthetic.
"""
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = os.getenv("APP_VERSION", "1.0.0")
PORT = int(os.getenv("PORT", "8080"))
INSTANCE = os.getenv("POD_NAME") or socket.gethostname()
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "registration")
DB_USER = os.getenv("DB_USER", "registration_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_TIMEOUT = float(os.getenv("DB_CHECK_TIMEOUT_SECONDS", "1.0"))
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "memory")  # memory | postgres
CHAOS_ENABLED = os.getenv("CHAOS_ENABLED", "false").lower() == "true"
STARTUP_DELAY = float(os.getenv("STARTUP_DELAY_SECONDS", "0"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [" + INSTANCE + "] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("registration")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- storage
class MemoryStore:
    """In-memory store; the DB is still checked over TCP so that the service
    behaves like a DB-backed service when the DB is down (used by the local
    simulator, where no PostgreSQL server is available)."""

    def __init__(self):
        self._rows = []
        self._lock = threading.Lock()

    def ping(self):
        with socket.create_connection((DB_HOST, DB_PORT), timeout=DB_TIMEOUT):
            return True

    def add(self, row):
        self.ping()
        with self._lock:
            self._rows.append(row)

    def find(self, student_id):
        self.ping()
        with self._lock:
            return [r for r in self._rows if r["student_id"] == student_id]


class PostgresStore:
    def __init__(self):
        import pg8000.native  # noqa: F401  (imported lazily, optional dependency)

        self._pg = pg8000.native
        self._local = threading.local()

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._pg.Connection(
                user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT,
                database=DB_NAME, timeout=DB_TIMEOUT * 3,
            )
            conn.run(
                "CREATE TABLE IF NOT EXISTS registrations ("
                " id TEXT PRIMARY KEY, student_id TEXT NOT NULL,"
                " course_code TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,"
                " served_by TEXT NOT NULL)"
            )
            self._local.conn = conn
        return conn

    def _run(self, sql, **params):
        try:
            return self._conn().run(sql, **params)
        except Exception:
            self._local.conn = None  # drop broken connection, reconnect next time
            raise

    def ping(self):
        self._run("SELECT 1")
        return True

    def add(self, row):
        self._run(
            "INSERT INTO registrations (id, student_id, course_code, created_at, served_by)"
            " VALUES (:id, :student_id, :course_code, :created_at, :served_by)",
            **row,
        )

    def find(self, student_id):
        rows = self._run(
            "SELECT id, student_id, course_code, created_at::text, served_by"
            " FROM registrations WHERE student_id = :sid ORDER BY created_at",
            sid=student_id,
        )
        keys = ("id", "student_id", "course_code", "created_at", "served_by")
        return [dict(zip(keys, r)) for r in rows]


STORE = PostgresStore() if STORAGE_BACKEND == "postgres" else MemoryStore()


# --------------------------------------------------------------------------- state
class State:
    started_at = time.monotonic()
    shutting_down = False
    hung = False  # set by /chaos/hang to simulate a deadlocked process


def db_ok():
    try:
        return STORE.ping()
    except Exception as exc:  # any DB error means "not ready"
        log.warning("DB check failed: %s", exc.__class__.__name__)
        return False


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "aitu-registration/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # route access log through logging
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, code, body, extra_headers=None):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Served-By", INSTANCE)
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    # --- GET
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/healthz/live":
            if State.hung:
                time.sleep(30)  # a hung process never answers in time
            return self._send(200, {"status": "alive", "instance": INSTANCE})
        if url.path == "/healthz/ready":
            if State.shutting_down:
                return self._send(503, {"status": "shutting-down", "instance": INSTANCE})
            if not db_ok():
                return self._send(503, {"status": "db-unavailable", "instance": INSTANCE})
            return self._send(200, {"status": "ready", "instance": INSTANCE})
        if url.path == "/api/v1/info":
            return self._send(200, {
                "service": "registration", "version": VERSION, "instance": INSTANCE,
                "uptime_s": round(time.monotonic() - State.started_at, 1),
            })
        if url.path == "/api/v1/registrations":
            sid = parse_qs(url.query).get("student_id", [""])[0]
            if not sid:
                return self._send(400, {"error": "student_id query parameter is required"})
            try:
                return self._send(200, {"items": STORE.find(sid)})
            except Exception:
                return self._send(503, {"error": "database unavailable"}, {"Retry-After": "5"})
        return self._send(404, {"error": "not found"})

    # --- POST
    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if url.path == "/chaos/hang" and CHAOS_ENABLED:
            State.hung = True
            log.error("CHAOS: liveness endpoint will now hang")
            return self._send(202, {"status": "liveness will hang"})
        if url.path != "/api/v1/registrations":
            return self._send(404, {"error": "not found"})
        try:
            data = json.loads(raw or b"{}")
            student_id = str(data["student_id"]).strip()
            course_code = str(data["course_code"]).strip()
            if not student_id or not course_code:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "student_id and course_code are required"})
        row = {
            "id": str(uuid.uuid4()), "student_id": student_id, "course_code": course_code,
            "created_at": utc_now(), "served_by": INSTANCE,
        }
        try:
            STORE.add(row)
        except Exception as exc:
            log.warning("registration rejected, DB error: %s", exc.__class__.__name__)
            # 503 + Retry-After: the client (gateway / frontend) may retry safely later.
            return self._send(503, {"error": "database unavailable"}, {"Retry-After": "5"})
        return self._send(201, row)


def main():
    if STARTUP_DELAY:
        log.info("simulating slow start: %.1fs", STARTUP_DELAY)
        time.sleep(STARTUP_DELAY)
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True

    def on_sigterm(signum, _frame):
        # Graceful shutdown: fail readiness first so the endpoint is removed,
        # then stop accepting connections.
        log.info("received signal %s, shutting down gracefully", signum)
        State.shutting_down = True
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)
    signal.signal(signal.SIGINT, on_sigterm)
    log.info("registration-service %s listening on :%d (storage=%s, db=%s:%d)",
             VERSION, PORT, STORAGE_BACKEND, DB_HOST, DB_PORT)
    httpd.serve_forever()
    log.info("stopped")


if __name__ == "__main__":
    main()

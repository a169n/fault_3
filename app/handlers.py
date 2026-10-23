"""HTTP endpoints: health checks, the registration API and the chaos hook.

Design decision: liveness does NOT depend on the database. If it did, a database
outage would make Kubernetes restart every healthy replica in a loop, which
adds load and hides the real root cause. Only readiness depends on the DB.
"""
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from config import CHAOS_ENABLED, INSTANCE, VERSION, log, utc_now
from store import STORE


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

#!/usr/bin/env python3
"""Synthetic AITU Registration Service (educational, Assignment 3) - entry point.

A deliberately small HTTP service used to demonstrate Kubernetes resilience
mechanisms. Its only dependency is the pure-Python PostgreSQL driver `pg8000`.

Modules
-------
config.py    settings from environment variables, logging
store.py     PostgreSQL storage (connection per thread, automatic reconnect)
handlers.py  HTTP endpoints and health checks
this file    server start-up and graceful shutdown

Endpoints
---------
GET  /healthz/live                 liveness  - is the process able to serve at all?
GET  /healthz/ready                readiness - can this replica do useful work (DB reachable)?
GET  /api/v1/info                  which replica answered (used to observe load balancing)
POST /api/v1/registrations         {"student_id": "S0001", "course_code": "CC-301"}
GET  /api/v1/registrations?student_id=S0001
POST /chaos/hang                   (only if CHAOS_ENABLED=true) make liveness fail

All data handled by this service is synthetic.
"""
import signal
import threading
import time
from http.server import ThreadingHTTPServer

from config import DB_HOST, DB_PORT, PORT, STARTUP_DELAY, VERSION, log
from handlers import Handler, State


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
    log.info("registration-service %s listening on :%d (db=%s:%d)",
             VERSION, PORT, DB_HOST, DB_PORT)
    httpd.serve_forever()
    log.info("stopped")


if __name__ == "__main__":
    main()

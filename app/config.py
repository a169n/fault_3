"""Settings of the Registration Service, read from environment variables.

Kubernetes injects them from the ConfigMap (DB_HOST, ...), the Secret
(DB_USER, DB_PASSWORD, DB_NAME) and the pod itself (POD_NAME).
"""
import logging
import os
import socket
import sys
from datetime import datetime, timezone

VERSION = os.getenv("APP_VERSION", "1.0.0")
PORT = int(os.getenv("PORT", "8080"))
INSTANCE = os.getenv("POD_NAME") or socket.gethostname()
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "registration")
DB_USER = os.getenv("DB_USER", "registration_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_TIMEOUT = float(os.getenv("DB_CHECK_TIMEOUT_SECONDS", "1.0"))
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

"""PostgreSQL storage for registrations (pure-Python driver pg8000)."""
import threading

import config


class PostgresStore:
    def __init__(self):
        import pg8000.native  # noqa: F401  (imported lazily, optional dependency)

        self._pg = pg8000.native
        self._local = threading.local()

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._pg.Connection(
                user=config.DB_USER, password=config.DB_PASSWORD, host=config.DB_HOST,
                port=config.DB_PORT, database=config.DB_NAME, timeout=config.DB_TIMEOUT * 3,
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


STORE = PostgresStore()

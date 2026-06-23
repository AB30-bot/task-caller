import sqlite3
import uuid
from datetime import datetime, timezone


class Database:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                task            TEXT NOT NULL,
                contact         TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'waiting',
                created_at      TEXT NOT NULL,
                link_sent_at    TEXT,
                call_started_at TEXT,
                call_ended_at   TEXT,
                transcript      TEXT,
                summary         TEXT
            )
        """)
        self._conn.commit()

    def create_job(self, task: str, contact: str) -> dict:
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO jobs (id, task, contact, status, created_at) VALUES (?, ?, ?, 'waiting', ?)",
            (job_id, task, contact, now),
        )
        self._conn.commit()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_job(self, job_id: str, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [job_id]
        self._conn.execute(f"UPDATE jobs SET {sets} WHERE id = ?", vals)
        self._conn.commit()

    def close(self):
        self._conn.close()

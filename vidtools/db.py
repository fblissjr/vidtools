"""SQLite-backed state for vidtools: file registry and job store.

Single connection per process, WAL mode, threading.Lock for cross-thread
safety. No SQLAlchemy, no migrations framework -- just direct SQL.
"""

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import orjson

from .jobs import Job, JobStatus
from .utils import logger


_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    path        TEXT NOT NULL,
    size        INTEGER NOT NULL,
    created_at  REAL NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('upload', 'output'))
);
CREATE INDEX IF NOT EXISTS idx_files_created ON files(created_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    operation        TEXT NOT NULL,
    params_json      TEXT NOT NULL,
    input_file       TEXT,
    url              TEXT,
    status           TEXT NOT NULL,
    progress         INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT NOT NULL DEFAULT '',
    error            TEXT,
    output_file      TEXT,
    created_at       REAL NOT NULL,
    started_at       REAL,
    completed_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""


class Database:
    """Connection lifecycle and thread safety for the SQLite database."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)

    @contextmanager
    def cursor(self):
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class FileRegistry:
    """File metadata storage. Replaces the in-memory dict + JSON file pattern."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, file_id: str, filename: str, path: str, size: int, source: str) -> None:
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO files (id, filename, path, size, created_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, filename, path, size, time.time(), source),
            )

    def get(self, file_id: str) -> dict | None:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM files WHERE id = ?", (file_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def delete(self, file_id: str) -> None:
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def list_all(self) -> list[dict]:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM files ORDER BY created_at DESC")
            return [dict(row) for row in cur.fetchall()]

    def prune_missing(self) -> int:
        """Remove entries whose path no longer exists. Returns count removed."""
        with self.db.cursor() as cur:
            cur.execute("SELECT id, path FROM files")
            missing_ids = [row["id"] for row in cur.fetchall()
                           if not Path(row["path"]).exists()]
            if not missing_ids:
                return 0
            placeholders = ",".join("?" * len(missing_ids))
            cur.execute(f"DELETE FROM files WHERE id IN ({placeholders})", missing_ids)
            return cur.rowcount

    def __contains__(self, file_id: str) -> bool:
        with self.db.cursor() as cur:
            cur.execute("SELECT 1 FROM files WHERE id = ? LIMIT 1", (file_id,))
            return cur.fetchone() is not None


class JobStore:
    """Job persistence. Live progress is overlaid by JobManager (in memory)
    -- the persisted progress field is a snapshot at status transition time."""

    def __init__(self, db: Database):
        self.db = db

    def insert(self, job: Job) -> None:
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (id, operation, params_json, input_file, url, status, "
                "progress, progress_message, error, output_file, created_at, started_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id, job.operation, orjson.dumps(job.params).decode(),
                    job.input_file, job.url, str(job.status),
                    job.progress, job.progress_message,
                    job.error, job.output_file,
                    job.created_at, job.started_at, job.completed_at,
                ),
            )

    def update_status(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
        error: str | None = None,
        output_file: str | None = None,
        progress: int | None = None,
        progress_message: str | None = None,
    ) -> None:
        """Update one or more fields. None means leave unchanged."""
        sets: list[str] = []
        values: list = []
        if status is not None:
            sets.append("status = ?")
            values.append(str(status))
        if started_at is not None:
            sets.append("started_at = ?")
            values.append(started_at)
        if completed_at is not None:
            sets.append("completed_at = ?")
            values.append(completed_at)
        if error is not None:
            sets.append("error = ?")
            values.append(error)
        if output_file is not None:
            sets.append("output_file = ?")
            values.append(output_file)
        if progress is not None:
            sets.append("progress = ?")
            values.append(progress)
        if progress_message is not None:
            sets.append("progress_message = ?")
            values.append(progress_message)
        if not sets:
            return
        values.append(job_id)
        with self.db.cursor() as cur:
            cur.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", values)

    def get(self, job_id: str) -> Job | None:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_all(self) -> list[Job]:
        with self.db.cursor() as cur:
            cur.execute("SELECT * FROM jobs ORDER BY created_at DESC")
            return [self._row_to_job(row) for row in cur.fetchall()]

    def delete_old(self, before_ts: float, terminal_only: bool = True) -> int:
        terminal = (str(JobStatus.COMPLETED), str(JobStatus.FAILED), str(JobStatus.CANCELLED))
        with self.db.cursor() as cur:
            if terminal_only:
                cur.execute(
                    f"DELETE FROM jobs WHERE completed_at IS NOT NULL "
                    f"AND completed_at < ? AND status IN ({','.join('?' * len(terminal))})",
                    (before_ts, *terminal),
                )
            else:
                cur.execute(
                    "DELETE FROM jobs WHERE created_at < ?",
                    (before_ts,),
                )
            return cur.rowcount

    def fail_unfinished(self, reason: str = "Server restart") -> int:
        """On startup, mark any RUNNING/QUEUED jobs as FAILED.
        Returns number of jobs affected."""
        with self.db.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = ?, error = ?, completed_at = ? "
                "WHERE status IN (?, ?)",
                (str(JobStatus.FAILED), reason, time.time(),
                 str(JobStatus.RUNNING), str(JobStatus.QUEUED)),
            )
            return cur.rowcount

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        params = orjson.loads(row["params_json"]) if row["params_json"] else {}
        return Job(
            id=row["id"],
            operation=row["operation"],
            params=params,
            input_file=row["input_file"],
            url=row["url"],
            status=JobStatus(row["status"]),
            progress=row["progress"],
            progress_message=row["progress_message"],
            error=row["error"],
            output_file=row["output_file"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )


def migrate_legacy_registry(registry: FileRegistry, legacy_path: Path) -> int:
    """One-time migration of the old file_registry.json into the SQLite registry.
    Returns number of entries migrated. Renames the file on success."""
    if not legacy_path.exists():
        return 0
    try:
        data = orjson.loads(legacy_path.read_bytes())
    except Exception:
        logger.exception("Could not parse legacy registry; skipping migration")
        return 0

    migrated = 0
    for fid, info in data.items():
        if fid in registry:
            continue
        path = info.get("path")
        if not path or not Path(path).exists():
            continue
        try:
            registry.add(fid, info["filename"], path, info["size"], info["source"])
            migrated += 1
        except Exception:
            logger.exception("Failed migrating entry", file_id=fid)

    legacy_path.rename(legacy_path.with_suffix(".json.migrated"))
    logger.info("Migrated legacy file registry", count=migrated)
    return migrated

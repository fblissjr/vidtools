"""Job queue system for vidtools web server.

In-memory job tracking with async execution support.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    """Represents a single processing job."""

    operation: str
    params: dict[str, Any]
    input_file: str | None = None
    url: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: JobStatus = JobStatus.QUEUED
    progress: int = 0
    progress_message: str = ""
    error: str | None = None
    output_file: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "operation": self.operation,
            "params": self.params,
            "input_file": self.input_file,
            "url": self.url,
            "status": self.status,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "error": self.error,
            "output_file": self.output_file,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def update_progress(self, percent: int, message: str = ""):
        """Update job progress. Used as callback from operations."""
        self.progress = min(100, max(0, percent))
        self.progress_message = message


class JobManager:
    """Manages job lifecycle: creation, tracking, execution, cleanup.

    Jobs persist to a JobStore at status transitions. Live progress (which
    fires rapidly during execution) is held in memory only -- it would create
    excessive WAL bloat to persist every tick.
    """

    def __init__(self, max_concurrent: int = 2, store=None):
        self.max_concurrent = max_concurrent
        self.store = store  # vidtools.db.JobStore | None (None for tests)
        self.jobs: dict[str, Job] = {}
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazy init semaphore (needs running event loop)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    def load_from_store(self) -> None:
        """Rehydrate in-memory jobs from the persistent store. Call at startup."""
        if self.store is None:
            return
        # Mark anything that was running/queued at last shutdown as failed
        self.store.fail_unfinished("Server restart")
        for job in self.store.list_all():
            self.jobs[job.id] = job

    def create_job(
        self,
        operation: str,
        params: dict,
        input_file: str | None = None,
        url: str | None = None,
    ) -> Job:
        """Create a new job and persist it."""
        job = Job(operation=operation, params=params, input_file=input_file, url=url)
        self.jobs[job.id] = job
        if self.store is not None:
            self.store.insert(job)
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self.jobs.values())

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job. Returns True if cancelled, False if not found or not cancellable."""
        job = self.jobs.get(job_id)
        if job is None:
            return False
        if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            job.status = JobStatus.CANCELLED
            job.completed_at = time.time()
            if self.store is not None:
                self.store.update_status(job_id, status=JobStatus.CANCELLED,
                                         completed_at=job.completed_at)
            return True
        return False

    def cleanup_old_jobs(self, max_age_seconds: int = 3600):
        """Remove completed/failed/cancelled jobs older than max_age."""
        now = time.time()
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
        to_remove = [
            jid
            for jid, job in self.jobs.items()
            if job.status in terminal
            and job.completed_at is not None
            and (now - job.completed_at) > max_age_seconds
        ]
        for jid in to_remove:
            del self.jobs[jid]
        if self.store is not None:
            self.store.delete_old(now - max_age_seconds, terminal_only=True)

    async def run_job(self, job: Job, work_fn: Callable):
        """Run a job with concurrency limiting.

        work_fn should accept (job) and do the actual work. It will be called
        via asyncio.to_thread for blocking operations. Progress is reported
        via the thread-local progress sink (see vidtools.progress).
        """
        sem = self._get_semaphore()
        async with sem:
            if job.status == JobStatus.CANCELLED:
                return

            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            if self.store is not None:
                self.store.update_status(job.id, status=JobStatus.RUNNING,
                                         started_at=job.started_at)

            try:
                await asyncio.to_thread(work_fn, job)
                if job.status == JobStatus.CANCELLED:
                    return
                job.status = JobStatus.COMPLETED
                job.progress = 100
                job.completed_at = time.time()
                if self.store is not None:
                    self.store.update_status(
                        job.id, status=JobStatus.COMPLETED,
                        completed_at=job.completed_at,
                        progress=100, output_file=job.output_file,
                    )
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)
                job.completed_at = time.time()
                if self.store is not None:
                    self.store.update_status(
                        job.id, status=JobStatus.FAILED,
                        completed_at=job.completed_at,
                        error=job.error,
                    )

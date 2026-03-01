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
    """Manages job lifecycle: creation, tracking, execution, cleanup."""

    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent
        self.jobs: dict[str, Job] = {}
        self._semaphore: asyncio.Semaphore | None = None
        self._progress_listeners: list[Callable] = []

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazy init semaphore (needs running event loop)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._semaphore

    def create_job(
        self,
        operation: str,
        params: dict,
        input_file: str | None = None,
        url: str | None = None,
    ) -> Job:
        """Create a new job and add it to the store."""
        job = Job(operation=operation, params=params, input_file=input_file, url=url)
        self.jobs[job.id] = job
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

    def add_progress_listener(self, listener: Callable):
        """Register a callback for progress updates."""
        self._progress_listeners.append(listener)

    def remove_progress_listener(self, listener: Callable):
        """Remove a progress listener."""
        try:
            self._progress_listeners.remove(listener)
        except ValueError:
            pass

    async def run_job(self, job: Job, work_fn: Callable):
        """Run a job with concurrency limiting.

        work_fn should accept (job, progress_callback) and do the actual work.
        It will be called via asyncio.to_thread for blocking operations.
        """
        sem = self._get_semaphore()
        async with sem:
            if job.status == JobStatus.CANCELLED:
                return

            job.status = JobStatus.RUNNING
            job.started_at = time.time()

            try:
                await self._notify_progress(job)
                await asyncio.to_thread(work_fn, job, job.update_progress)
                if job.status == JobStatus.CANCELLED:
                    return
                job.status = JobStatus.COMPLETED
                job.progress = 100
                job.completed_at = time.time()
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = str(e)
                job.completed_at = time.time()
            finally:
                await self._notify_progress(job)

    async def _notify_progress(self, job: Job):
        """Notify all registered progress listeners."""
        for listener in self._progress_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(job)
                else:
                    listener(job)
            except Exception:
                pass

"""Tests for vidtools.jobs module."""

import asyncio
import time

import pytest

from vidtools.jobs import Job, JobManager, JobStatus


class TestJobStatus:
    def test_status_values(self):
        assert JobStatus.QUEUED == "queued"
        assert JobStatus.RUNNING == "running"
        assert JobStatus.COMPLETED == "completed"
        assert JobStatus.FAILED == "failed"
        assert JobStatus.CANCELLED == "cancelled"


class TestJob:
    def test_job_creation(self):
        job = Job(
            operation="convert",
            params={"format": "mp4"},
            input_file="test.avi",
        )
        assert job.id is not None
        assert job.status == JobStatus.QUEUED
        assert job.operation == "convert"
        assert job.progress == 0
        assert job.error is None
        assert job.output_file is None

    def test_job_to_dict(self):
        job = Job(
            operation="resize",
            params={"percentage": 0.5},
            input_file="video.mp4",
        )
        d = job.to_dict()
        assert d["id"] == job.id
        assert d["operation"] == "resize"
        assert d["status"] == "queued"
        assert d["progress"] == 0
        assert "created_at" in d

    def test_job_unique_ids(self):
        j1 = Job(operation="a", params={}, input_file="a.mp4")
        j2 = Job(operation="b", params={}, input_file="b.mp4")
        assert j1.id != j2.id


class TestJobManager:
    @pytest.fixture
    def manager(self):
        return JobManager(max_concurrent=2)

    def test_create_job(self, manager):
        job = manager.create_job("convert", {"format": "mp4"}, input_file="test.avi")
        assert job.id in manager.jobs
        assert job.status == JobStatus.QUEUED

    def test_get_job(self, manager):
        job = manager.create_job("resize", {}, input_file="video.mp4")
        fetched = manager.get_job(job.id)
        assert fetched is job

    def test_get_nonexistent_job(self, manager):
        assert manager.get_job("nonexistent") is None

    def test_list_jobs(self, manager):
        manager.create_job("a", {}, input_file="a.mp4")
        manager.create_job("b", {}, input_file="b.mp4")
        jobs = manager.list_jobs()
        assert len(jobs) == 2

    def test_cancel_queued_job(self, manager):
        job = manager.create_job("convert", {}, input_file="test.mp4")
        result = manager.cancel_job(job.id)
        assert result is True
        assert job.status == JobStatus.CANCELLED

    def test_cancel_nonexistent_job(self, manager):
        assert manager.cancel_job("nope") is False

    def test_max_concurrent_limit(self, manager):
        """Manager should track concurrent limit."""
        assert manager.max_concurrent == 2

    def test_cleanup_old_jobs(self, manager):
        job = manager.create_job("convert", {}, input_file="test.mp4")
        job.status = JobStatus.COMPLETED
        job.completed_at = time.time() - 7200  # 2 hours ago
        manager.cleanup_old_jobs(max_age_seconds=3600)
        assert job.id not in manager.jobs

    def test_cleanup_keeps_recent_jobs(self, manager):
        job = manager.create_job("convert", {}, input_file="test.mp4")
        job.status = JobStatus.COMPLETED
        job.completed_at = time.time() - 100  # 100 seconds ago
        manager.cleanup_old_jobs(max_age_seconds=3600)
        assert job.id in manager.jobs

    def test_cleanup_keeps_running_jobs(self, manager):
        job = manager.create_job("convert", {}, input_file="test.mp4")
        job.status = JobStatus.RUNNING
        manager.cleanup_old_jobs(max_age_seconds=0)
        assert job.id in manager.jobs

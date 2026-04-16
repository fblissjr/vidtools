"""Tests for SQLite-backed file registry and job store."""
import time
import pytest
from vidtools.db import Database, FileRegistry, JobStore
from vidtools.jobs import Job, JobStatus


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(db_path)
    yield database
    database.close()


@pytest.fixture
def registry(db):
    return FileRegistry(db)


@pytest.fixture
def store(db):
    return JobStore(db)


class TestDatabase:
    def test_creates_db_file(self, tmp_path):
        db_path = tmp_path / "new.db"
        assert not db_path.exists()
        db = Database(db_path)
        assert db_path.exists()
        db.close()

    def test_reopens_existing_db(self, tmp_path):
        db_path = tmp_path / "reopen.db"
        db1 = Database(db_path)
        db1.close()
        # Should not raise on reopen
        db2 = Database(db_path)
        db2.close()


class TestFileRegistry:
    def test_add_and_get(self, registry, tmp_path):
        # Use real path for the entry to satisfy any prune logic
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        registry.add("abc123", "video.mp4", str(f), 4, "upload")
        info = registry.get("abc123")
        assert info is not None
        assert info["filename"] == "video.mp4"
        assert info["size"] == 4
        assert info["source"] == "upload"

    def test_get_missing(self, registry):
        assert registry.get("nonexistent") is None

    def test_delete(self, registry, tmp_path):
        f = tmp_path / "x.mp4"
        f.write_bytes(b"y")
        registry.add("xyz", "x.mp4", str(f), 1, "upload")
        registry.delete("xyz")
        assert registry.get("xyz") is None

    def test_list_all(self, registry, tmp_path):
        for i in range(3):
            f = tmp_path / f"f{i}.mp4"
            f.write_bytes(b"z")
            registry.add(f"id{i}", f"f{i}.mp4", str(f), 1, "upload")
        assert len(registry.list_all()) == 3

    def test_contains(self, registry, tmp_path):
        f = tmp_path / "y.mp4"
        f.write_bytes(b"z")
        registry.add("abc", "y.mp4", str(f), 1, "upload")
        assert "abc" in registry
        assert "nope" not in registry

    def test_prune_missing(self, registry, tmp_path):
        existing = tmp_path / "exists.mp4"
        existing.write_bytes(b"x")
        registry.add("real", "exists.mp4", str(existing), 1, "upload")
        registry.add("ghost", "ghost.mp4", "/nonexistent/path.mp4", 1, "upload")

        removed = registry.prune_missing()
        assert removed == 1
        assert "real" in registry
        assert "ghost" not in registry

    def test_invalid_source_rejected(self, registry, tmp_path):
        f = tmp_path / "y.mp4"
        f.write_bytes(b"z")
        with pytest.raises(Exception):  # SQLite IntegrityError or wrapper
            registry.add("bad", "y.mp4", str(f), 1, "invalid_source")


class TestJobStore:
    def _make_job(self, op="resize", input_file="abc"):
        return Job(operation=op, params={"percentage": 0.5}, input_file=input_file)

    def test_insert_and_get(self, store):
        job = self._make_job()
        store.insert(job)
        loaded = store.get(job.id)
        assert loaded is not None
        assert loaded.operation == "resize"
        assert loaded.params == {"percentage": 0.5}
        assert loaded.input_file == "abc"

    def test_get_missing(self, store):
        assert store.get("nonexistent") is None

    def test_update_status(self, store):
        job = self._make_job()
        store.insert(job)
        now = time.time()
        store.update_status(job.id, status=JobStatus.RUNNING, started_at=now)
        loaded = store.get(job.id)
        assert loaded.status == JobStatus.RUNNING
        assert loaded.started_at == pytest.approx(now)

    def test_list_all(self, store):
        for _ in range(3):
            store.insert(self._make_job())
        assert len(store.list_all()) == 3

    def test_delete_old_terminal_only(self, store):
        old_completed = self._make_job()
        store.insert(old_completed)
        store.update_status(old_completed.id,
                            status=JobStatus.COMPLETED,
                            completed_at=time.time() - 7200)

        recent_completed = self._make_job()
        store.insert(recent_completed)
        store.update_status(recent_completed.id,
                            status=JobStatus.COMPLETED,
                            completed_at=time.time())

        running_old = self._make_job()
        store.insert(running_old)
        store.update_status(running_old.id, status=JobStatus.RUNNING)

        before = time.time() - 3600
        removed = store.delete_old(before, terminal_only=True)
        assert removed == 1
        assert store.get(old_completed.id) is None
        assert store.get(recent_completed.id) is not None
        assert store.get(running_old.id) is not None  # not terminal

    def test_url_field_persists(self, store):
        job = Job(operation="ytdlp", params={"format": "mp3"}, url="https://example.com/x")
        store.insert(job)
        loaded = store.get(job.id)
        assert loaded.url == "https://example.com/x"
        assert loaded.input_file is None

    def test_fail_unfinished_marks_running_and_queued(self, store):
        running = self._make_job()
        store.insert(running)
        store.update_status(running.id, status=JobStatus.RUNNING)

        queued = self._make_job()
        store.insert(queued)  # default status is QUEUED

        completed = self._make_job()
        store.insert(completed)
        store.update_status(completed.id, status=JobStatus.COMPLETED,
                            completed_at=time.time())

        affected = store.fail_unfinished("test reason")
        assert affected == 2
        assert store.get(running.id).status == JobStatus.FAILED
        assert store.get(running.id).error == "test reason"
        assert store.get(queued.id).status == JobStatus.FAILED
        assert store.get(completed.id).status == JobStatus.COMPLETED


class TestPersistenceAcrossReopens:
    def test_jobs_persist(self, tmp_path):
        db_path = tmp_path / "persist.db"
        db1 = Database(db_path)
        store1 = JobStore(db1)
        job = Job(operation="resize", params={"percentage": 0.5}, input_file="abc")
        store1.insert(job)
        db1.close()

        db2 = Database(db_path)
        store2 = JobStore(db2)
        loaded = store2.get(job.id)
        assert loaded is not None
        assert loaded.operation == "resize"
        db2.close()

    def test_files_persist(self, tmp_path):
        db_path = tmp_path / "persist.db"
        f = tmp_path / "v.mp4"
        f.write_bytes(b"x")

        db1 = Database(db_path)
        reg1 = FileRegistry(db1)
        reg1.add("xyz", "v.mp4", str(f), 1, "upload")
        db1.close()

        db2 = Database(db_path)
        reg2 = FileRegistry(db2)
        assert "xyz" in reg2
        db2.close()


class TestMigration:
    def test_migrates_legacy_registry(self, tmp_path, registry):
        from vidtools.db import migrate_legacy_registry
        import orjson

        f = tmp_path / "real.mp4"
        f.write_bytes(b"x")

        legacy = tmp_path / "file_registry.json"
        legacy.write_bytes(orjson.dumps({
            "abc": {"filename": "real.mp4", "path": str(f), "size": 1, "source": "upload"},
            "ghost": {"filename": "g.mp4", "path": "/no/such/path", "size": 1, "source": "upload"},
        }))

        count = migrate_legacy_registry(registry, legacy)
        assert count == 1  # ghost is skipped (file doesn't exist)
        assert "abc" in registry
        assert "ghost" not in registry
        assert not legacy.exists()
        assert legacy.with_suffix(".json.migrated").exists()

    def test_missing_legacy_file_is_noop(self, tmp_path, registry):
        from vidtools.db import migrate_legacy_registry
        result = migrate_legacy_registry(registry, tmp_path / "nonexistent.json")
        assert result == 0

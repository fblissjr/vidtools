"""Tests for vidtools.server module -- file registry integration."""

import pytest
from vidtools.db import Database, FileRegistry, migrate_legacy_registry
import orjson


class TestServerFileRegistryIntegration:
    """The server now uses FileRegistry from vidtools.db. The unit-level
    behavior is tested in test_db.py; these tests verify the server-side
    integration concerns: legacy migration and lifespan setup."""

    def test_legacy_migration_runs_on_startup(self, tmp_path):
        # Simulate a pre-existing JSON registry that the server should migrate
        f = tmp_path / "real.mp4"
        f.write_bytes(b"fake video")

        legacy = tmp_path / "file_registry.json"
        legacy.write_bytes(orjson.dumps({
            "abc123": {
                "filename": "real.mp4",
                "path": str(f),
                "size": 10,
                "source": "upload",
            },
        }))

        db = Database(tmp_path / "vidtools.db")
        registry = FileRegistry(db)
        try:
            migrated = migrate_legacy_registry(registry, legacy)
            assert migrated == 1
            assert "abc123" in registry
            # Legacy file should be renamed
            assert not legacy.exists()
            assert legacy.with_suffix(".json.migrated").exists()
        finally:
            db.close()

    def test_database_persists_across_reopens(self, tmp_path):
        db_path = tmp_path / "vidtools.db"
        f = tmp_path / "v.mp4"
        f.write_bytes(b"x")

        db1 = Database(db_path)
        registry1 = FileRegistry(db1)
        registry1.add("xyz", "v.mp4", str(f), 1, "upload")
        db1.close()

        db2 = Database(db_path)
        registry2 = FileRegistry(db2)
        try:
            assert "xyz" in registry2
            info = registry2.get("xyz")
            assert info["filename"] == "v.mp4"
        finally:
            db2.close()

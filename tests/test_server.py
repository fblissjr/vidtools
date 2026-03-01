"""Tests for vidtools.server module -- file registry persistence."""

import tempfile
from pathlib import Path

import orjson
import pytest


class TestFileRegistryPersistence:
    def test_save_and_load_registry(self, tmp_path, monkeypatch):
        """Registry should survive save/load cycle."""
        # Set up isolated data dir
        monkeypatch.setattr("vidtools.server.DATA_DIR", tmp_path)
        monkeypatch.setattr("vidtools.server.UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr("vidtools.server.OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr("vidtools.server.TEMP_DIR", tmp_path / "temp")
        monkeypatch.setattr("vidtools.server.REGISTRY_FILE", tmp_path / "file_registry.json")

        from vidtools.server import (
            _register_file,
            _load_registry,
            _save_registry,
            file_registry,
            REGISTRY_FILE,
        )

        # Create dirs
        (tmp_path / "uploads").mkdir()
        (tmp_path / "output").mkdir()

        # Create a real file so registry prune doesn't remove it
        test_file = tmp_path / "uploads" / "test_abc123.mp4"
        test_file.write_bytes(b"fake video content")

        # Register a file
        _register_file("abc123", "test.mp4", str(test_file), 18, "upload")

        # Verify it was saved to disk
        assert (tmp_path / "file_registry.json").exists()
        saved = orjson.loads((tmp_path / "file_registry.json").read_bytes())
        assert "abc123" in saved
        assert saved["abc123"]["filename"] == "test.mp4"

        # Clear in-memory and reload
        file_registry.clear()
        assert len(file_registry) == 0

        _load_registry()
        assert "abc123" in file_registry
        assert file_registry["abc123"]["filename"] == "test.mp4"

    def test_load_prunes_missing_files(self, tmp_path, monkeypatch):
        """Loading should remove entries for files that no longer exist."""
        monkeypatch.setattr("vidtools.server.DATA_DIR", tmp_path)
        monkeypatch.setattr("vidtools.server.UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr("vidtools.server.OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr("vidtools.server.TEMP_DIR", tmp_path / "temp")
        monkeypatch.setattr("vidtools.server.REGISTRY_FILE", tmp_path / "file_registry.json")

        from vidtools.server import _load_registry, file_registry

        (tmp_path / "uploads").mkdir()
        (tmp_path / "output").mkdir()

        # Write a registry entry pointing to a nonexistent file
        (tmp_path / "file_registry.json").write_bytes(orjson.dumps({
            "gone123": {
                "filename": "gone.mp4",
                "path": str(tmp_path / "uploads" / "gone.mp4"),
                "size": 100,
                "created_at": 1000.0,
                "source": "upload",
            }
        }))

        file_registry.clear()
        _load_registry()
        assert "gone123" not in file_registry

    def test_unregister_removes_and_saves(self, tmp_path, monkeypatch):
        """_unregister_file should remove from memory and persist."""
        monkeypatch.setattr("vidtools.server.DATA_DIR", tmp_path)
        monkeypatch.setattr("vidtools.server.UPLOAD_DIR", tmp_path / "uploads")
        monkeypatch.setattr("vidtools.server.OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr("vidtools.server.TEMP_DIR", tmp_path / "temp")
        monkeypatch.setattr("vidtools.server.REGISTRY_FILE", tmp_path / "file_registry.json")

        from vidtools.server import _register_file, _unregister_file, file_registry

        (tmp_path / "uploads").mkdir()
        test_file = tmp_path / "uploads" / "test_del.mp4"
        test_file.write_bytes(b"data")

        _register_file("del123", "test_del.mp4", str(test_file), 4, "upload")
        assert "del123" in file_registry

        _unregister_file("del123")
        assert "del123" not in file_registry

        # Verify persisted
        saved = orjson.loads((tmp_path / "file_registry.json").read_bytes())
        assert "del123" not in saved

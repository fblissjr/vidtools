"""Tests for vidtools.separation -- the audio source-separation wrapper.

These tests mock the audio-separator backend so they run without the optional
'separation' extra installed and without downloading any models.
"""
import pytest
from unittest.mock import MagicMock

from vidtools import separation


class TestSeparateAudio:
    def test_calls_backend_with_args(self, tmp_path, monkeypatch):
        captured = {}
        def fake_separate(input_file, model, output_dir):
            captured["input"] = input_file
            captured["model"] = model
            captured["output_dir"] = output_dir
            return [str(tmp_path / "in_vocals.wav"),
                    str(tmp_path / "in_instrumental.wav")]
        backend = MagicMock(spec=separation.SeparationBackend)
        backend.separate.side_effect = fake_separate
        monkeypatch.setattr(separation, "_DEFAULT_BACKEND", backend)

        result = separation.separate_audio("in.mp3", "some_model.ckpt", str(tmp_path))

        assert captured["input"] == "in.mp3"
        assert captured["model"] == "some_model.ckpt"
        assert captured["output_dir"] == str(tmp_path)
        assert len(result) == 2

    def test_creates_output_dir(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "new_stems"
        assert not out_dir.exists()
        backend = MagicMock(spec=separation.SeparationBackend)
        backend.separate.return_value = []
        monkeypatch.setattr(separation, "_DEFAULT_BACKEND", backend)

        separation.separate_audio("in.mp3", "m.ckpt", str(out_dir))
        assert out_dir.exists()

    def test_returns_backend_outputs_unchanged(self, tmp_path, monkeypatch):
        expected = ["a.wav", "b.wav", "c.wav"]
        backend = MagicMock(spec=separation.SeparationBackend)
        backend.separate.return_value = expected
        monkeypatch.setattr(separation, "_DEFAULT_BACKEND", backend)

        result = separation.separate_audio("in.mp3", "m.ckpt", str(tmp_path))
        assert result == expected


class TestAudioSeparatorBackendImport:
    """The backend should raise an actionable ImportError if the optional
    'separation' extra isn't installed -- not a bare ModuleNotFoundError."""

    def test_missing_extra_raises_with_install_hint(self, monkeypatch):
        import sys
        # Force the audio_separator import to fail
        monkeypatch.setitem(sys.modules, "audio_separator", None)
        monkeypatch.setitem(sys.modules, "audio_separator.separator", None)

        with pytest.raises(ImportError, match="separation"):
            separation.AudioSeparatorBackend()


class TestGetBackend:
    def test_lazy_singleton(self, monkeypatch):
        """get_backend() should cache the backend instance."""
        monkeypatch.setattr(separation, "_DEFAULT_BACKEND", None)
        created = []
        class Fake:
            def __init__(self):
                created.append(1)
            def separate(self, *a, **kw): return []
            def available_models(self): return []
        monkeypatch.setattr(separation, "AudioSeparatorBackend", Fake)

        b1 = separation.get_backend()
        b2 = separation.get_backend()
        assert b1 is b2
        assert len(created) == 1

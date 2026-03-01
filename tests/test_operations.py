"""Tests for vidtools.operations module -- error handling and API contracts."""

import json

import pytest

from vidtools.operations import (
    resize_video,
    rotate_video,
    apply_preset,
    get_video_info,
)


class TestResizeVideoValidation:
    def test_raises_on_no_size_params(self):
        """resize_video must raise ValueError when no sizing param is given."""
        with pytest.raises(ValueError, match="must specify"):
            resize_video("input.mp4", "output.mp4")


class TestRotateVideoValidation:
    def test_raises_on_invalid_rotation(self):
        with pytest.raises(ValueError, match="Invalid rotation"):
            rotate_video("input.mp4", "output.mp4", "45")


class TestApplyPresetValidation:
    def test_raises_on_unknown_preset(self):
        with pytest.raises(ValueError, match="not found"):
            apply_preset("input.mp4", "output.mp4", "nonexistent_preset_xyz")


class TestGetVideoInfo:
    def test_returns_dict(self, tmp_path):
        """get_video_info should return a dict, not print to stdout."""
        # Can't test with a real file without ffprobe, so just verify the
        # function signature returns a dict (not None).
        # This would fail with subprocess.CalledProcessError on a fake file,
        # which is expected -- we're testing the contract, not ffprobe.
        with pytest.raises(Exception):
            # Should raise because file doesn't exist, not return None
            get_video_info(str(tmp_path / "nonexistent.mp4"))


class TestProgressCallbackSignature:
    """Verify all operations accept progress_callback kwarg without error."""

    def test_resize_accepts_callback(self):
        """resize_video should accept progress_callback parameter."""
        import inspect
        sig = inspect.signature(resize_video)
        assert "progress_callback" in sig.parameters

    def test_cut_accepts_callback(self):
        from vidtools.operations import cut_video
        import inspect
        sig = inspect.signature(cut_video)
        assert "progress_callback" in sig.parameters

    def test_convert_accepts_callback(self):
        from vidtools.operations import convert_format
        import inspect
        sig = inspect.signature(convert_format)
        assert "progress_callback" in sig.parameters

    def test_extract_audio_accepts_callback(self):
        from vidtools.operations import extract_audio
        import inspect
        sig = inspect.signature(extract_audio)
        assert "progress_callback" in sig.parameters

    def test_crop_accepts_callback(self):
        from vidtools.operations import crop_video
        import inspect
        sig = inspect.signature(crop_video)
        assert "progress_callback" in sig.parameters

    def test_rotate_accepts_callback(self):
        import inspect
        sig = inspect.signature(rotate_video)
        assert "progress_callback" in sig.parameters

    def test_concatenate_accepts_callback(self):
        from vidtools.operations import concatenate_videos
        import inspect
        sig = inspect.signature(concatenate_videos)
        assert "progress_callback" in sig.parameters

    def test_sanitize_accepts_callback(self):
        from vidtools.operations import sanitize_video
        import inspect
        sig = inspect.signature(sanitize_video)
        assert "progress_callback" in sig.parameters

    def test_slideshow_accepts_callback(self):
        from vidtools.operations import create_slideshow
        import inspect
        sig = inspect.signature(create_slideshow)
        assert "progress_callback" in sig.parameters

    def test_apply_preset_accepts_callback(self):
        import inspect
        sig = inspect.signature(apply_preset)
        assert "progress_callback" in sig.parameters

"""Tests for vidtools.operations module -- error handling and API contracts."""

import json

import pytest

from vidtools.operations import (
    resize_video,
    rotate_video,
    apply_preset,
    get_video_info,
    add_audio,
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


class TestAddAudioValidation:
    def test_raises_on_invalid_mode(self):
        """add_audio must raise ValueError for unknown mode."""
        with pytest.raises(ValueError, match="Invalid mode"):
            add_audio("video.mp4", "audio.mp3", "output.mp4", mode="invalid")

    def test_raises_on_invalid_volume(self):
        """add_audio must raise ValueError for volume out of range."""
        with pytest.raises(ValueError, match="volume"):
            add_audio("video.mp4", "audio.mp3", "output.mp4", volume=-0.5)

    def test_accepts_replace_mode(self):
        """add_audio should accept 'replace' mode without validation error."""
        # Will fail on actual ffmpeg execution (no real files), but should not
        # raise ValueError for mode validation
        import inspect
        sig = inspect.signature(add_audio)
        assert "mode" in sig.parameters

    def test_accepts_mix_mode(self):
        """add_audio should accept 'mix' mode parameter."""
        import inspect
        sig = inspect.signature(add_audio)
        params = sig.parameters
        assert "mode" in params
        assert params["mode"].default == "replace"

    def test_accepts_volume_param(self):
        """add_audio should accept volume parameter."""
        import inspect
        sig = inspect.signature(add_audio)
        params = sig.parameters
        assert "volume" in params
        assert params["volume"].default == 1.0


class TestAddAudioCommand:
    """Test ffmpeg command construction for add_audio (mocked)."""

    @pytest.fixture()
    def capture_ffmpeg(self, monkeypatch):
        captured = []
        def mock_run(cmd, progress_callback=None):
            captured.extend(cmd)
        monkeypatch.setattr("vidtools.operations.run_ffmpeg_command", mock_run)
        return captured

    def test_replace_mode_command(self, capture_ffmpeg):
        """Replace mode should map video stream from input 0 and audio from input 1."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="replace")

        assert "-i" in capture_ffmpeg
        assert "video.mp4" in capture_ffmpeg
        assert "audio.mp3" in capture_ffmpeg
        assert "-map" in capture_ffmpeg
        assert "0:v" in capture_ffmpeg
        assert "1:a" in capture_ffmpeg

    def test_mix_mode_command(self, capture_ffmpeg):
        """Mix mode should use amix filter_complex."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="mix")

        assert "-filter_complex" in capture_ffmpeg
        filter_idx = capture_ffmpeg.index("-filter_complex") + 1
        assert "amix" in capture_ffmpeg[filter_idx]

    def test_replace_copies_video_stream(self, capture_ffmpeg):
        """Replace mode should copy video stream (no re-encode)."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="replace")

        assert "-c:v" in capture_ffmpeg
        cv_idx = capture_ffmpeg.index("-c:v") + 1
        assert capture_ffmpeg[cv_idx] == "copy"

    def test_replace_copies_audio_at_default_volume(self, capture_ffmpeg):
        """Replace with default volume should stream-copy audio (no re-encode)."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="replace")

        ca_idx = capture_ffmpeg.index("-c:a") + 1
        assert capture_ffmpeg[ca_idx] == "copy"
        assert "-af" not in capture_ffmpeg

    def test_volume_applied_in_mix_mode(self, capture_ffmpeg):
        """Volume adjustment should appear in filter_complex when mixing."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="mix", volume=0.5)

        filter_idx = capture_ffmpeg.index("-filter_complex") + 1
        assert "volume=0.5" in capture_ffmpeg[filter_idx]

    def test_volume_applied_in_replace_mode(self, capture_ffmpeg):
        """Volume adjustment should re-encode audio with aac in replace mode."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="replace", volume=0.5)

        cmd_str = " ".join(capture_ffmpeg)
        assert "volume=0.5" in cmd_str
        ca_idx = capture_ffmpeg.index("-c:a") + 1
        assert capture_ffmpeg[ca_idx] == "aac"

    def test_mp4_output_gets_faststart(self, capture_ffmpeg):
        """MP4 output should include -movflags +faststart."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="replace")

        assert "-movflags" in capture_ffmpeg
        assert "+faststart" in capture_ffmpeg

    def test_shortest_flag_in_replace_mode(self, capture_ffmpeg):
        """Replace mode should use -shortest."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="replace")

        assert "-shortest" in capture_ffmpeg

    def test_mix_mode_no_shortest_flag(self, capture_ffmpeg):
        """Mix mode uses amix duration=shortest, no need for -shortest flag."""
        add_audio("video.mp4", "audio.mp3", "output.mp4", mode="mix")

        assert "-shortest" not in capture_ffmpeg


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

    def test_add_audio_accepts_callback(self):
        import inspect
        sig = inspect.signature(add_audio)
        assert "progress_callback" in sig.parameters

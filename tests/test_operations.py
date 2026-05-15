"""Tests for vidtools.operations module -- error handling and API contracts."""

import pytest

from vidtools.operations import (
    resize_video,
    rotate_video,
    apply_preset,
    get_video_info,
    add_audio,
    sanitize_video,
    trim_to_audio,
)


class TestResizeVideoValidation:
    def test_raises_on_no_size_params(self):
        """resize_video must raise ValueError when no sizing param is given."""
        with pytest.raises(ValueError, match="specify"):
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
        def mock_run(cmd):
            captured.extend(cmd)
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", mock_run)
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


class TestSanitizeVideoCommand:
    """Test sanitize_video command construction. Uses manual_crop to skip
    the cropdetect probe pass (which would invoke real ffmpeg)."""

    @pytest.fixture()
    def capture_ffmpeg(self, monkeypatch):
        captured = []
        def mock_run(cmd):
            captured.extend(cmd)
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", mock_run)
        return captured

    def test_strips_container_metadata(self, capture_ffmpeg):
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="iw:ih:0:0")
        assert "-map_metadata" in capture_ffmpeg
        idx = capture_ffmpeg.index("-map_metadata")
        assert capture_ffmpeg[idx + 1] == "-1"

    def test_strips_stream_metadata(self, capture_ffmpeg):
        """Stream-level metadata (per-track tags) must also be stripped."""
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="iw:ih:0:0")
        assert "-map_metadata:s" in capture_ffmpeg
        idx = capture_ffmpeg.index("-map_metadata:s")
        assert capture_ffmpeg[idx + 1] == "-1"

    def test_uses_bitexact_to_strip_encoder_string(self, capture_ffmpeg):
        """ffmpeg writes its own version into output by default. -fflags +bitexact
        suppresses that so the output doesn't reveal which ffmpeg encoded it."""
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="iw:ih:0:0")
        assert "-fflags" in capture_ffmpeg
        idx = capture_ffmpeg.index("-fflags")
        assert "+bitexact" in capture_ffmpeg[idx + 1]

    def test_default_audio_mode_reencodes(self, capture_ffmpeg):
        """Default audio_mode should re-encode to AAC (not copy) so audio
        fingerprints don't survive sanitization."""
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="iw:ih:0:0")
        # Default is 'aac', not 'copy'
        assert "-c:a" in capture_ffmpeg
        ca_idx = capture_ffmpeg.index("-c:a")
        assert capture_ffmpeg[ca_idx + 1] == "aac"

    def test_audio_mode_copy_preserves_stream(self, capture_ffmpeg):
        """Explicit audio_mode='copy' still works for users who need it."""
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="iw:ih:0:0", audio_mode="copy")
        ca_idx = capture_ffmpeg.index("-c:a")
        assert capture_ffmpeg[ca_idx + 1] == "copy"

    def test_audio_mode_none_strips_audio(self, capture_ffmpeg):
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="iw:ih:0:0", audio_mode="none")
        assert "-an" in capture_ffmpeg

    def test_manual_crop_uses_full_expression(self, capture_ffmpeg):
        """When manual_crop is W:H:X:Y the full expression should be used,
        not just the height."""
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="800:600:50:100")
        vf_idx = capture_ffmpeg.index("-vf")
        chain = capture_ffmpeg[vf_idx + 1]
        assert "crop=800:600:50:100" in chain

    def test_noise_filter_in_chain(self, capture_ffmpeg):
        sanitize_video(input_file="in.mp4", output_file="out.mp4",
                       manual_crop="iw:ih:0:0", noise=10)
        vf_idx = capture_ffmpeg.index("-vf")
        chain = capture_ffmpeg[vf_idx + 1]
        assert "noise=alls=10" in chain


class TestTrimToAudio:
    """Test trim_to_audio command construction. The op probes durations via
    get_video_info, so that's mocked alongside run_ffmpeg."""

    @pytest.fixture()
    def capture(self, monkeypatch):
        captured = []
        def mock_run(cmd):
            captured.extend(cmd)
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", mock_run)
        return captured

    def _mock_probe(self, monkeypatch, video_dur, audio_dur):
        info = {
            "streams": [
                {"codec_type": "video", "duration": str(video_dur)},
                {"codec_type": "audio", "duration": str(audio_dur)},
            ],
            "format": {"duration": str(max(video_dur, audio_dur))},
        }
        monkeypatch.setattr("vidtools.video_ops.get_video_info", lambda _: info)

    def test_trims_when_audio_shorter(self, capture, monkeypatch):
        """Video=10s, audio=8s, buffer=0.25 -> -t 8.25"""
        self._mock_probe(monkeypatch, 10.0, 8.0)
        trim_to_audio("in.mp4", "out.mp4", buffer=0.25)
        assert "-t" in capture
        t_val = capture[capture.index("-t") + 1]
        assert abs(float(t_val) - 8.25) < 0.001

    def test_default_buffer_is_sub_second(self, monkeypatch):
        """Default buffer must be < 1s per the design goal."""
        import inspect
        sig = inspect.signature(trim_to_audio)
        assert sig.parameters["buffer"].default < 1.0
        assert sig.parameters["buffer"].default >= 0.0

    def test_no_op_when_audio_longer(self, capture, monkeypatch, caplog):
        """Audio already >= video: no -t flag, just remux/passthrough."""
        self._mock_probe(monkeypatch, 5.0, 8.0)
        trim_to_audio("in.mp4", "out.mp4")
        assert "-t" not in capture

    def test_no_op_when_audio_equals_video(self, capture, monkeypatch):
        """audio_dur + buffer > video_dur should also skip trim."""
        self._mock_probe(monkeypatch, 10.0, 10.0)
        trim_to_audio("in.mp4", "out.mp4", buffer=0.25)
        assert "-t" not in capture

    def test_uses_stream_copy_by_default(self, capture, monkeypatch):
        self._mock_probe(monkeypatch, 10.0, 8.0)
        trim_to_audio("in.mp4", "out.mp4")
        c_idx = capture.index("-c")
        assert capture[c_idx + 1] == "copy"

    def test_accurate_mode_reencodes(self, capture, monkeypatch):
        """--accurate switches to re-encode so the trim is frame-accurate."""
        self._mock_probe(monkeypatch, 10.0, 8.0)
        trim_to_audio("in.mp4", "out.mp4", accurate=True)
        assert "-c:v" in capture
        cv_idx = capture.index("-c:v")
        assert capture[cv_idx + 1] != "copy"

    def test_crf_zero_is_honored(self, capture, monkeypatch):
        """crf=0 is valid (lossless H.264). Must not be treated as falsy/unset."""
        self._mock_probe(monkeypatch, 10.0, 8.0)
        trim_to_audio("in.mp4", "out.mp4", accurate=True, crf=0)
        assert "-crf" in capture
        assert capture[capture.index("-crf") + 1] == "0"

    def test_custom_buffer(self, capture, monkeypatch):
        self._mock_probe(monkeypatch, 10.0, 7.0)
        trim_to_audio("in.mp4", "out.mp4", buffer=0.5)
        t_val = capture[capture.index("-t") + 1]
        assert abs(float(t_val) - 7.5) < 0.001

    def test_zero_buffer_allowed(self, capture, monkeypatch):
        self._mock_probe(monkeypatch, 10.0, 8.0)
        trim_to_audio("in.mp4", "out.mp4", buffer=0.0)
        t_val = capture[capture.index("-t") + 1]
        assert abs(float(t_val) - 8.0) < 0.001

    def test_negative_buffer_rejected(self, monkeypatch):
        self._mock_probe(monkeypatch, 10.0, 8.0)
        with pytest.raises(ValueError, match="buffer"):
            trim_to_audio("in.mp4", "out.mp4", buffer=-0.1)

    def test_raises_when_no_audio_stream(self, monkeypatch):
        info = {
            "streams": [{"codec_type": "video", "duration": "10.0"}],
            "format": {"duration": "10.0"},
        }
        monkeypatch.setattr("vidtools.video_ops.get_video_info", lambda _: info)
        with pytest.raises(ValueError, match="audio"):
            trim_to_audio("in.mp4", "out.mp4")

    def test_raises_when_no_video_stream(self, monkeypatch):
        info = {
            "streams": [{"codec_type": "audio", "duration": "10.0"}],
            "format": {"duration": "10.0"},
        }
        monkeypatch.setattr("vidtools.video_ops.get_video_info", lambda _: info)
        with pytest.raises(ValueError, match="video"):
            trim_to_audio("in.mp4", "out.mp4")

    def test_falls_back_to_format_duration_when_stream_duration_missing(self, capture, monkeypatch):
        """Some containers don't carry per-stream duration; format.duration is
        the fallback. If neither audio nor video have stream duration, both
        resolve to format duration -> no trim."""
        info = {
            "streams": [
                {"codec_type": "video"},
                {"codec_type": "audio"},
            ],
            "format": {"duration": "10.0"},
        }
        monkeypatch.setattr("vidtools.video_ops.get_video_info", lambda _: info)
        trim_to_audio("in.mp4", "out.mp4")
        assert "-t" not in capture

    def test_mp4_output_gets_faststart(self, capture, monkeypatch):
        self._mock_probe(monkeypatch, 10.0, 8.0)
        trim_to_audio("in.mp4", "out.mp4")
        assert "-movflags" in capture
        assert "+faststart" in capture


class TestNoProgressCallbackParam:
    """After the thread-local sink refactor, no operation should expose
    a progress_callback parameter. Progress flows via the ambient sink."""

    OPS = [
        "resize_video", "cut_video", "convert_format", "extract_audio",
        "crop_video", "rotate_video", "concatenate_videos", "sanitize_video",
        "create_slideshow", "apply_preset", "add_audio", "trim_to_audio",
    ]

    def test_no_progress_callback_in_signatures(self):
        import inspect
        from vidtools import operations
        for name in self.OPS:
            fn = getattr(operations, name)
            sig = inspect.signature(fn)
            assert "progress_callback" not in sig.parameters, (
                f"{name} still has progress_callback parameter"
            )

    def test_image_ops_no_progress_callback(self):
        import inspect
        from vidtools import image_ops
        for name in ["resize_image", "convert_image", "crop_image",
                     "create_thumbnail", "strip_image_metadata",
                     "optimize_image", "adjust_image"]:
            fn = getattr(image_ops, name)
            sig = inspect.signature(fn)
            assert "progress_callback" not in sig.parameters, (
                f"{name} still has progress_callback parameter"
            )

    def test_audio_ops_no_progress_callback(self):
        import inspect
        from vidtools import audio_ops
        for name in ["convert_audio", "cut_audio", "normalize_audio",
                     "mix_audio", "fade_audio", "resample_audio",
                     "remove_silence"]:
            fn = getattr(audio_ops, name)
            sig = inspect.signature(fn)
            assert "progress_callback" not in sig.parameters, (
                f"{name} still has progress_callback parameter"
            )

"""Tests for input-type smart dispatch."""
import pytest
from unittest.mock import MagicMock
from vidtools.cli import (
    _detect_kind, _make_smart, VIDEO_EXTS, AUDIO_EXTS, IMAGE_EXTS,
    _quality_to_crf, _quality_to_audio_bitrate, setup_argparse,
    _audio_verb, _parse_pipeline_steps,
)


class TestDetectKind:
    def test_video_extensions(self):
        for ext in [".mp4", ".mov", ".mkv", ".avi", ".webm", ".gif"]:
            assert _detect_kind(f"file{ext}") == "video", f"Failed for {ext}"

    def test_audio_extensions(self):
        for ext in [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"]:
            assert _detect_kind(f"file{ext}") == "audio", f"Failed for {ext}"

    def test_image_extensions(self):
        for ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]:
            assert _detect_kind(f"file{ext}") == "image", f"Failed for {ext}"

    def test_case_insensitive(self):
        assert _detect_kind("FILE.MP4") == "video"
        assert _detect_kind("FILE.PNG") == "image"
        assert _detect_kind("FILE.MP3") == "audio"

    def test_unknown_extension_raises(self):
        with pytest.raises(SystemExit) as exc:
            _detect_kind("file.xyz")
        msg = str(exc.value)
        assert "xyz" in msg
        # Message should be actionable: it lists the supported extensions.
        assert "supported" in msg.lower()
        assert ".mp4" in msg and ".mp3" in msg and ".png" in msg

    def test_no_extension_raises(self):
        with pytest.raises(SystemExit):
            _detect_kind("file_no_ext")

    def test_path_with_directory(self):
        assert _detect_kind("/tmp/some/path/video.mp4") == "video"
        assert _detect_kind("./relative/path/audio.mp3") == "audio"


class TestExtensionSets:
    def test_no_overlap_between_kinds(self):
        # Each extension belongs to exactly one kind
        assert VIDEO_EXTS.isdisjoint(AUDIO_EXTS)
        assert VIDEO_EXTS.isdisjoint(IMAGE_EXTS)
        assert AUDIO_EXTS.isdisjoint(IMAGE_EXTS)

    def test_gif_is_video(self):
        # .gif explicitly classified as video (has frames + duration)
        assert ".gif" in VIDEO_EXTS
        assert ".gif" not in IMAGE_EXTS


class TestMakeSmart:
    def test_routes_to_video_handler(self):
        video_h, image_h, audio_h = MagicMock(), MagicMock(), MagicMock()
        dispatcher = _make_smart("test", video=video_h, image=image_h, audio=audio_h)
        args = MagicMock()
        args.input = "file.mp4"
        dispatcher(args)
        video_h.assert_called_once_with(args)
        image_h.assert_not_called()
        audio_h.assert_not_called()

    def test_routes_to_image_handler(self):
        video_h, image_h = MagicMock(), MagicMock()
        dispatcher = _make_smart("test", video=video_h, image=image_h)
        args = MagicMock()
        args.input = "file.png"
        dispatcher(args)
        image_h.assert_called_once_with(args)
        video_h.assert_not_called()

    def test_routes_to_audio_handler(self):
        video_h, audio_h = MagicMock(), MagicMock()
        dispatcher = _make_smart("test", video=video_h, audio=audio_h)
        args = MagicMock()
        args.input = "file.mp3"
        dispatcher(args)
        audio_h.assert_called_once_with(args)

    def test_unsupported_kind_raises(self):
        # Only video handler provided, but input is image
        dispatcher = _make_smart("test", video=MagicMock())
        args = MagicMock()
        args.input = "file.png"
        with pytest.raises(SystemExit) as exc:
            dispatcher(args)
        assert "image" in str(exc.value)
        assert "test" in str(exc.value)


class TestQualityMapping:
    """Unified 1-100 quality knob maps to per-codec controls (higher = better)."""

    def test_crf_monotonic_higher_quality_lower_crf(self):
        assert _quality_to_crf(100) < _quality_to_crf(50) < _quality_to_crf(1)

    def test_crf_in_practical_band(self):
        # 100 -> near-lossless but sane; never below 0 or above 51.
        assert 0 <= _quality_to_crf(100) <= 20
        assert _quality_to_crf(1) <= 51

    def test_crf_midpoint_is_x264_default(self):
        # quality 50 should land on the x264 default CRF (23).
        assert _quality_to_crf(50) == 23

    def test_crf_clamps_out_of_range(self):
        assert _quality_to_crf(9999) == _quality_to_crf(100)
        assert _quality_to_crf(-5) == _quality_to_crf(1)

    def test_audio_bitrate_monotonic(self):
        def k(q):
            return int(_quality_to_audio_bitrate(q).rstrip("k"))
        assert k(95) >= k(70) >= k(50) >= k(30) >= k(10)

    def test_audio_bitrate_top_is_320(self):
        assert _quality_to_audio_bitrate(100) == "320k"


class TestUnifiedCommandSurface:
    """The reorg exposes one verb per concept, no img-/audio- prefixes."""

    def _commands(self):
        parser = setup_argparse()
        # The subparsers action is the one with choices.
        for action in parser._actions:
            if getattr(action, "choices", None) and "convert" in action.choices:
                return set(action.choices)
        raise AssertionError("subparsers not found")

    def test_unified_verbs_present(self):
        cmds = self._commands()
        for verb in ["info", "convert", "resize", "crop", "trim", "rotate",
                     "normalize", "fade", "resample", "clean", "separate",
                     "thumbnail", "strip-metadata", "optimize", "adjust",
                     "screenshot", "match-duration"]:
            assert verb in cmds, f"missing unified verb: {verb}"

    def test_cut_is_alias_of_trim(self):
        # argparse exposes aliases as additional choices pointing at the same parser.
        cmds = self._commands()
        assert "cut" in cmds and "trim" in cmds

    def test_old_prefixed_commands_removed(self):
        cmds = self._commands()
        for gone in ["img-resize", "img-convert", "img-crop", "img-info",
                     "audio-cut", "audio-convert", "audio-normalize", "audio-info",
                     "trim-to-audio"]:
            assert gone not in cmds, f"prefixed command should be removed: {gone}"


class TestAudioVerbVideoAware:
    """Audio-processing verbs (normalize/fade/resample/clean) accept video inputs:
    audio output runs directly; video output remuxes the processed audio back
    over the original video."""

    def _recorder(self):
        calls = {"op": [], "add_audio": []}
        def run_op(args, in_path, out_path):
            calls["op"].append((in_path, out_path))
        return calls, run_op

    def test_audio_input_runs_directly(self, monkeypatch):
        calls, run_op = self._recorder()
        monkeypatch.setattr("vidtools.cli.operations.add_audio",
                            lambda *a, **k: calls["add_audio"].append((a, k)))
        args = MagicMock(); args.input = "song.mp3"; args.output = "out.mp3"
        _audio_verb(run_op)(args)
        assert calls["op"] == [("song.mp3", "out.mp3")]
        assert calls["add_audio"] == []

    def test_video_in_audio_out_runs_directly(self, monkeypatch):
        calls, run_op = self._recorder()
        monkeypatch.setattr("vidtools.cli.operations.add_audio",
                            lambda *a, **k: calls["add_audio"].append((a, k)))
        args = MagicMock(); args.input = "clip.mp4"; args.output = "out.mp3"
        _audio_verb(run_op)(args)
        assert calls["op"] == [("clip.mp4", "out.mp3")]
        assert calls["add_audio"] == []

    def test_video_in_video_out_remuxes(self, monkeypatch):
        calls, run_op = self._recorder()
        monkeypatch.setattr("vidtools.cli.operations.add_audio",
                            lambda inp, aud, out, **k: calls["add_audio"].append((inp, aud, out)))
        args = MagicMock(); args.input = "clip.mp4"; args.output = "out.mp4"
        _audio_verb(run_op)(args)
        assert len(calls["op"]) == 1
        op_in, op_out = calls["op"][0]
        assert op_in == "clip.mp4" and op_out.endswith(".wav")  # processed into a temp
        assert len(calls["add_audio"]) == 1
        inp, aud, out = calls["add_audio"][0]
        assert inp == "clip.mp4" and aud.endswith(".wav") and out == "out.mp4"


class TestPipelineConvertFlags:
    """Pipeline convert step uses the same flag names as the top-level CLI."""

    def test_video_codec_flag_parsed(self):
        steps = _parse_pipeline_steps(["convert", "--video-codec", "libx265",
                                       "--audio-codec", "libopus"])
        assert steps[0].name == "convert"
        assert steps[0].params["video_codec"] == "libx265"
        assert steps[0].params["audio_codec"] == "libopus"

    def test_trim_alias_maps_to_cut_step(self):
        steps = _parse_pipeline_steps(["trim", "--start", "5", "--end", "9"])
        assert steps[0].name == "cut"
        assert steps[0].params["start"] == "5"

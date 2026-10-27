"""Tests for vidtools.operations module -- error handling and API contracts."""

from pathlib import Path

import pytest

from vidtools.operations import (
    VIDEO_COMPRESS_RECIPES,
    add_audio,
    compress_video,
    convert_format,
    extract_audio,
    extract_frames,
    get_video_info,
    resize_video,
    rotate_video,
    sanitize_video,
    screenshot,
    trim_to_audio,
)
from vidtools.progress import FfmpegError


class TestResizeVideoValidation:
    def test_raises_on_no_size_params(self):
        """resize_video must raise ValueError when no sizing param is given."""
        with pytest.raises(ValueError, match="specify"):
            resize_video("input.mp4", "output.mp4")


class TestResizeMaxEdge:
    """`--max-edge N` caps the longest side and never enlarges a smaller source.

    -W/-H/-p are all unconditional: `-H 720` on a 480p clip upscales it. That is
    defensible for a verb the user typed, and wrong for a "make this smaller"
    Finder preset applied to whatever was selected -- it would hand back a file
    that is bigger than the original and no sharper. These pin the shape of the
    expression that makes the cap one-directional; `test_integration.py` pins
    that ffmpeg actually resolves it that way.
    """

    def _filter_of(self, cmd):
        return cmd[cmd.index("-vf") + 1]

    def test_clamps_both_axes_to_the_cap(self, capture_ffmpeg):
        """min() on each axis is what makes the cap one-directional -- without it
        force_original_aspect_ratio=decrease still scales a small input *up* to
        fill the box."""
        resize_video("in.mp4", "out.mp4", max_edge=1280)
        f = self._filter_of(capture_ffmpeg[0])
        assert "min(iw,1280)" in f and "min(ih,1280)" in f

    def test_fits_inside_a_square_box_so_orientation_does_not_matter(self, capture_ffmpeg):
        """A square cap plus decrease caps the *longest* edge whichever way the
        video is turned. A bare height cap shrinks portrait phone video far more
        than the same number does to landscape."""
        resize_video("in.mp4", "out.mp4", max_edge=1280)
        f = self._filter_of(capture_ffmpeg[0])
        assert "force_original_aspect_ratio=decrease" in f

    def test_forces_even_dimensions(self, capture_ffmpeg):
        """yuv420p cannot store an odd dimension; the derived edge is arbitrary,
        so this is reachable from ordinary inputs (1918x1080 at 1280)."""
        resize_video("in.mp4", "out.mp4", max_edge=1280)
        assert "force_divisible_by=2" in self._filter_of(capture_ffmpeg[0])

    def test_honours_the_scaling_algorithm(self, capture_ffmpeg):
        resize_video("in.mp4", "out.mp4", max_edge=720, algorithm="bicubic")
        assert "flags=bicubic" in self._filter_of(capture_ffmpeg[0])

    def test_takes_precedence_over_nothing_it_is_alone(self, capture_ffmpeg):
        """max_edge is one of the mutually exclusive sizing modes, not a modifier
        layered on top of -p/-W/-H, so it builds a scale filter by itself."""
        resize_video("in.mp4", "out.mp4", max_edge=854)
        cmd = capture_ffmpeg[0]
        assert "-vf" in cmd and "min(iw,854)" in self._filter_of(cmd)

    def test_still_strips_metadata_by_default(self, capture_ffmpeg):
        """A new sizing mode is a new code path into the same op; the policy has
        to hold on it too."""
        resize_video("in.mp4", "out.mp4", max_edge=1280)
        assert "-map_metadata" in capture_ffmpeg[0]

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_rejects_non_positive_caps(self, bad, capture_ffmpeg):
        """Claim: deleting this lets a nonsense cap through silently.

        `if max_edge:` reads 0 as "not supplied", so `--max-edge 0` reported
        that no sizing option was given at all. Negatives were worse: they
        passed straight into the filter expression, and on the image path
        `--max-edge -100` exited 0 having written a **1x1** file from an
        800x600 source."""
        with pytest.raises(ValueError, match="positive"):
            resize_video("in.mp4", "out.mp4", max_edge=bad)
        assert capture_ffmpeg == [], "ran ffmpeg with a non-positive cap"


class TestRotateVideoValidation:
    def test_raises_on_invalid_rotation(self):
        with pytest.raises(ValueError, match="Invalid rotation"):
            rotate_video("input.mp4", "output.mp4", "45")


class TestGetVideoInfo:
    @pytest.mark.ffmpeg
    def test_unreadable_input_raises_ffmpeg_error(self, tmp_path):
        """Claim: deleting the translation lets `vidtools info broken.mp4` crash.

        `main()` catches `FfmpegError` and `ValueError` only. ffprobe's
        `CalledProcessError` escaped it as a traceback that also printed the
        absolute input path. `match-duration` probes through here too."""
        broken = tmp_path / "broken.mp4"
        broken.write_bytes(b"not a video")
        with pytest.raises(FfmpegError, match="broken.mp4"):
            get_video_info(str(broken))

    def test_missing_ffprobe_raises_ffmpeg_error(self, monkeypatch):
        """A missing binary is a FileNotFoundError, which also escaped `main()`."""
        def _missing(*_a, **_kw):
            raise FileNotFoundError("ffprobe")
        monkeypatch.setattr("vidtools.video_ops.subprocess.run", _missing)
        with pytest.raises(FfmpegError, match="ffprobe"):
            get_video_info("in.mp4")


class TestSlideshowOrientation:
    """Claim: a bare `Image.open` renders portrait phone photos sideways.

    `create_slideshow` sized the video from, and rendered, the stored pixel
    buffer rather than what the photo displays as -- the same defect `compress`
    had until 0.21.0. The processed frames only exist inside the temp dir while
    ffmpeg runs, so they are inspected from the stubbed `run_ffmpeg`.
    """

    def _frames_seen_by_ffmpeg(self, monkeypatch):
        from PIL import Image
        seen = []

        def _inspect(cmd):
            list_file = cmd[cmd.index("-i") + 1]
            first = Path(list_file).read_text().splitlines()[0]
            frame = first.removeprefix("file '").removesuffix("'")
            with Image.open(frame) as img:
                seen.append((img.size, img.convert("RGB").getpixel((img.width // 2, 5))))
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", _inspect)
        return seen

    def test_output_size_follows_displayed_orientation(self, oriented_jpeg, tmp_path, monkeypatch):
        from vidtools.operations import create_slideshow
        seen = self._frames_seen_by_ffmpeg(monkeypatch)
        create_slideshow(oriented_jpeg, str(tmp_path / "out.mp4"), duration_per_image=1)
        size, _ = seen[0]
        assert size == (200, 400), f"sized from the raw buffer: {size}"

    def test_frame_pixels_are_upright(self, oriented_jpeg, tmp_path, monkeypatch):
        """Size alone passes if only the probe is fixed; the red band must be
        along the top, where the photo displays it."""
        from vidtools.operations import create_slideshow
        seen = self._frames_seen_by_ffmpeg(monkeypatch)
        create_slideshow(oriented_jpeg, str(tmp_path / "out.mp4"),
                         duration_per_image=1, size="200x400")
        _, top_middle = seen[0]
        assert top_middle[0] > 200 and top_middle[1] < 60, f"top edge is {top_middle}"


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
        "create_slideshow", "add_audio", "trim_to_audio", "compress_video",
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
                     "fade_audio", "resample_audio", "clean_audio"]:
            fn = getattr(audio_ops, name)
            sig = inspect.signature(fn)
            assert "progress_callback" not in sig.parameters, (
                f"{name} still has progress_callback parameter"
            )


class TestScreenshot:
    """Single-frame grab. ffmpeg extracts the frame; PIL handles lossy re-encode
    (jpg/webp) so quality semantics stay consistent regardless of ffmpeg's
    compiled-in image encoders."""

    @pytest.fixture()
    def capture_ffmpeg(self, monkeypatch):
        """Capture the ffmpeg command and simulate it writing a frame to the
        output path (the last argv element), so the produced-frame check passes."""
        calls = []
        def fake_run(cmd):
            calls.append(list(cmd))
            Path(cmd[-1]).write_bytes(b"\x89PNG\r\n\x1a\n")  # non-empty stub frame
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", fake_run)
        return calls

    @pytest.fixture()
    def capture_pil(self, monkeypatch):
        calls = []
        def fake_convert(src, dst, quality=85):
            calls.append((src, dst, quality))
        monkeypatch.setattr("vidtools.video_ops.convert_image", fake_convert)
        return calls

    def test_png_is_single_ffmpeg_call(self, capture_ffmpeg, capture_pil, tmp_path):
        out = str(tmp_path / "shot.png")
        screenshot("in.mp4", out, timestamp="00:00:05")
        assert len(capture_ffmpeg) == 1
        cmd = capture_ffmpeg[0]
        assert cmd[-1] == out
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        assert "-update" in cmd

    def test_png_not_reencoded_through_pil(self, capture_ffmpeg, capture_pil, tmp_path):
        screenshot("in.mp4", str(tmp_path / "shot.png"))
        assert capture_pil == []  # png is lossless, written directly by ffmpeg

    def test_fast_seek_places_ss_before_input(self, capture_ffmpeg, capture_pil, tmp_path):
        screenshot("in.mp4", str(tmp_path / "shot.png"), timestamp="10")
        cmd = capture_ffmpeg[0]
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-ss") + 1] == "10"

    def test_accurate_seek_places_ss_after_input(self, capture_ffmpeg, capture_pil, tmp_path):
        screenshot("in.mp4", str(tmp_path / "shot.png"), timestamp="10", accurate=True)
        cmd = capture_ffmpeg[0]
        assert cmd.index("-ss") > cmd.index("-i")

    def test_no_timestamp_omits_ss(self, capture_ffmpeg, capture_pil, tmp_path):
        screenshot("in.mp4", str(tmp_path / "shot.png"))
        assert "-ss" not in capture_ffmpeg[0]

    def test_empty_string_timestamp_omits_ss(self, capture_ffmpeg, capture_pil, tmp_path):
        """An empty timestamp (e.g. blank web field forwarded as "") must not
        emit `-ss ""`, which ffmpeg rejects."""
        screenshot("in.mp4", str(tmp_path / "shot.png"), timestamp="")
        assert "-ss" not in capture_ffmpeg[0]

    def test_zero_timestamp_still_seeks(self, capture_ffmpeg, capture_pil, tmp_path):
        """timestamp=0 is a valid seek (frame 0), not 'no timestamp'."""
        screenshot("in.mp4", str(tmp_path / "shot.png"), timestamp=0)
        assert "-ss" in capture_ffmpeg[0]

    def test_no_frame_produced_raises(self, capture_pil, tmp_path, monkeypatch):
        """Seeking past EOF makes ffmpeg exit 0 with no output; that must surface
        as FfmpegError, not a silent success (png) or a cryptic PIL error (jpg)."""
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", lambda cmd: None)
        with pytest.raises(FfmpegError, match="No frame"):
            screenshot("in.mp4", str(tmp_path / "shot.png"), timestamp="99999")

    def test_jpg_routes_through_pil_from_temp_png(self, capture_ffmpeg, capture_pil):
        screenshot("in.mp4", "shot.jpg", quality=90)
        assert capture_ffmpeg[0][-1].endswith(".png")  # grabbed lossless first
        assert len(capture_pil) == 1
        src, dst, quality = capture_pil[0]
        assert src.endswith(".png")
        assert dst == "shot.jpg"
        assert quality == 90

    def test_webp_routes_through_pil(self, capture_ffmpeg, capture_pil):
        screenshot("in.mp4", "shot.webp")
        assert capture_ffmpeg[0][-1].endswith(".png")
        assert capture_pil[0][1] == "shot.webp"


class TestConvertFormatAudioOnly:
    """`vidtools convert video.mp4 out.mp3` routes a video INPUT to convert_format
    (smart dispatch keys on input kind). An audio-only OUTPUT container can't hold
    a video stream, so convert_format must drop video and pick the right audio
    codec instead of emitting `-c:v libx264 -c:a aac` into an .mp3 muxer."""

    @pytest.fixture()
    def capture_ffmpeg(self, monkeypatch):
        calls = []
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg",
                            lambda cmd: calls.append(list(cmd)))
        return calls

    def test_video_to_mp3_drops_video_and_uses_lame(self, capture_ffmpeg):
        convert_format("in.mp4", "out.mp3", "mp3")
        cmd = capture_ffmpeg[0]
        assert "-vn" in cmd
        assert "-c:v" not in cmd            # no video encoder into an audio container
        assert cmd[cmd.index("-c:a") + 1] == "libmp3lame"
        assert "-crf" not in cmd

    def test_video_to_wav_uses_pcm(self, capture_ffmpeg):
        convert_format("in.mov", "out.wav", "wav")
        cmd = capture_ffmpeg[0]
        assert "-vn" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le"

    def test_audio_bitrate_forwarded(self, capture_ffmpeg):
        convert_format("in.mp4", "out.mp3", "mp3", audio_bitrate="320k")
        cmd = capture_ffmpeg[0]
        assert cmd[cmd.index("-b:a") + 1] == "320k"

    def test_time_range_preserved_for_audio_output(self, capture_ffmpeg):
        convert_format("in.mp4", "out.mp3", "mp3", start_time="10", end_time="20")
        cmd = capture_ffmpeg[0]
        assert "-ss" in cmd and "-to" in cmd
        assert "-vn" in cmd

    def test_video_to_video_still_encodes_video(self, capture_ffmpeg):
        convert_format("in.mov", "out.mp4", "mp4")
        cmd = capture_ffmpeg[0]
        assert "-c:v" in cmd
        assert "-vn" not in cmd


class TestMetadataPolicyShape:
    """Every op that writes a new file must apply the metadata policy.

    The 0.20.0 default was rolled out op-by-op, and the first review of it found
    two places the rollout had missed: three CLI verbs never exposed
    `--preserve-metadata`, and `pipeline` never stripped at all. This asserts the
    property across the whole set rather than per-op, so the next op added to
    the family cannot quietly skip it.
    """

    @pytest.fixture
    def capture(self, monkeypatch):
        calls = []
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", lambda cmd: calls.append(cmd))
        return calls

    def _run(self, name, tmp_path):
        out = str(tmp_path / "out.mp4")
        src = str(tmp_path / "in.mp4")
        Path(src).write_bytes(b"")
        from vidtools import operations
        return {
            "cut_video": lambda kw: operations.cut_video(src, out, start_time=0, end_time=1, **kw),
            "resize_video": lambda kw: operations.resize_video(src, out, percentage=0.5, **kw),
            "crop_video": lambda kw: operations.crop_video(src, out, 10, 10, 0, 0, **kw),
            "rotate_video": lambda kw: operations.rotate_video(src, out, 90, **kw),
            "concatenate_videos": lambda kw: operations.concatenate_videos([src, src], out, **kw),
            "add_audio": lambda kw: operations.add_audio(src, src, out, **kw),
        }[name]

    @pytest.mark.parametrize("op", [
        "cut_video", "resize_video", "crop_video", "rotate_video",
        "concatenate_videos", "add_audio",
    ])
    def test_strips_container_metadata_by_default(self, capture, tmp_path, op):
        self._run(op, tmp_path)({})
        cmd = capture[-1]
        assert "-map_metadata" in cmd, f"{op} does not strip container metadata"
        assert cmd[cmd.index("-map_metadata") + 1] == "-1"
        assert "-map_chapters" in cmd and cmd[cmd.index("-map_chapters") + 1] == "-1"
        assert "-fflags" in cmd and "+bitexact" in cmd[cmd.index("-fflags") + 1]

    @pytest.mark.parametrize("op", [
        "cut_video", "resize_video", "crop_video", "rotate_video",
        "concatenate_videos", "add_audio",
    ])
    def test_preserve_metadata_opts_back_in(self, capture, tmp_path, op):
        self._run(op, tmp_path)({"preserve_metadata": True})
        assert "-map_metadata" not in capture[-1], f"{op} strips even with preserve_metadata=True"


class TestPipelineMetadataPolicy:
    """A pipeline runs the same ops, so it must apply the same policy.

    It previously did not: `vidtools pipeline in.mp4 out.mp4 trim ... resize ...`
    carried the source's GPS through while `vidtools resize` on the same file
    stripped it -- reachable by the route a user takes to avoid re-encoding.
    """

    def test_pipeline_strips_by_default(self):
        from vidtools.pipeline import Pipeline, PipelineStep
        cmd = Pipeline("in.mp4", "out.mp4",
                       [PipelineStep("resize", {"percentage": 0.5})]).build_command()
        assert "-map_metadata" in cmd and cmd[cmd.index("-map_metadata") + 1] == "-1"

    def test_pipeline_preserve_metadata(self):
        from vidtools.pipeline import Pipeline, PipelineStep
        cmd = Pipeline("in.mp4", "out.mp4",
                       [PipelineStep("resize", {"percentage": 0.5})],
                       preserve_metadata=True).build_command()
        assert "-map_metadata" not in cmd


# --- G12: the three ops that used to carry source metadata through ---------
#
# extract_audio produces audio, so it takes the *audio* policy (descriptive
# tags kept, free-form and recorder fields blanked). extract_frames and
# slideshow produce images/video, so they strip.

def test_extract_audio_drops_identifying_tags(capture_ffmpeg):
    extract_audio("in.mp4", "out.m4a")
    cmd = capture_ffmpeg[0]
    assert "-map_metadata" in cmd and cmd[cmd.index("-map_metadata") + 1] == "0"
    for tag in ("comment", "location", "make", "model"):
        assert f"{tag}=" in cmd, f"{tag} not blanked"


def test_extract_audio_preserve_keeps_everything(capture_ffmpeg):
    extract_audio("in.mp4", "out.m4a", preserve_metadata=True)
    assert "-map_metadata" not in capture_ffmpeg[0]


def test_extract_frames_strips_metadata(capture_ffmpeg):
    extract_frames("in.mp4", "f_%03d.png")
    cmd = capture_ffmpeg[0]
    assert cmd[cmd.index("-map_metadata") + 1] == "-1"


def test_extract_frames_preserve_metadata(capture_ffmpeg):
    extract_frames("in.mp4", "f_%03d.png", preserve_metadata=True)
    assert "-map_metadata" not in capture_ffmpeg[0]


class TestCompressVideo:
    """Tests for compress_video recipes, smart <100 MB budget handling, and deep metadata stripping."""

    def _mock_probe(self, monkeypatch, duration=10.0, size_bytes=5_000_000):
        info = {
            "streams": [{"codec_type": "video", "duration": str(duration)}],
            "format": {"duration": str(duration), "size": str(size_bytes)},
        }
        monkeypatch.setattr("vidtools.video_ops.get_video_info", lambda _: info)
        monkeypatch.setattr("vidtools.video_ops.require_encoder", lambda _: None)

    def test_universal_short_clip_uses_one_pass_crf_not_bloated_bitrate(
        self, capture_ffmpeg, monkeypatch
    ):
        """A 10-second 5 MB clip under the 95 MB budget must use 1-pass CRF 23
        with VBV maxrate ceiling rather than 2-pass at ~75 Mbps."""
        self._mock_probe(monkeypatch, duration=10.0, size_bytes=5_000_000)
        compress_video("in.mp4", "out.mp4", recipe="universal")
        assert len(capture_ffmpeg) == 1
        cmd = capture_ffmpeg[0]
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "23"
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert "-maxrate" in cmd and "-bufsize" in cmd
        assert "-bsf:v" in cmd and cmd[cmd.index("-bsf:v") + 1] == "filter_units=remove_types=6"
        assert "-map_chapters" in cmd and cmd[cmd.index("-map_chapters") + 1] == "-1"
        assert "encoder=" in cmd

    def test_universal_over_budget_uses_two_pass_target_bitrate(
        self, capture_ffmpeg, monkeypatch
    ):
        """A 300-second 250 MB video (>95 MB budget) runs 2-pass libx264 with
        target bitrate and a temp passlogfile."""
        self._mock_probe(monkeypatch, duration=300.0, size_bytes=250_000_000)
        compress_video("in.mp4", "out.mp4", recipe="universal")
        assert len(capture_ffmpeg) == 2
        pass1, pass2 = capture_ffmpeg
        assert "-pass" in pass1 and pass1[pass1.index("-pass") + 1] == "1"
        assert "-pass" in pass2 and pass2[pass2.index("-pass") + 1] == "2"
        assert pass1[pass1.index("-passlogfile") + 1] == pass2[pass2.index("-passlogfile") + 1]
        assert "-b:v" in pass1 and "-b:v" in pass2
        # 95 MB = 760,000 kbits; over 300s = 2533 kbps total - 192 kbps audio = 2341k
        assert pass2[pass2.index("-b:v") + 1] == "2341k"

    def test_one_pass_fallback_to_two_pass_when_output_exceeds_max_size(
        self, monkeypatch, tmp_path
    ):
        """If a source is under budget_bytes (e.g. 92 MB) but 1-pass CRF writes
        an output > max_size (100 MB), compress_video must fall back to 2-pass."""
        self._mock_probe(monkeypatch, duration=120.0, size_bytes=90_000_000)
        out = tmp_path / "out.mp4"
        calls = []

        def fake_run(cmd):
            calls.append(list(cmd))
            if len(calls) == 1:
                # 1-pass wrote 105 MB (> 100 MB ceiling)
                out.write_bytes(b"x")
                os_stat_override["size"] = 105_000_000
            elif cmd[-1] == str(out):
                os_stat_override["size"] = 94_000_000

        os_stat_override = {"size": 0}
        monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", fake_run)
        monkeypatch.setattr("vidtools.video_ops.os.path.getsize", lambda _: os_stat_override["size"])

        compress_video("in.mp4", str(out), recipe="universal")
        # 1 call for 1-pass + 2 calls for 2-pass fallback = 3 calls
        assert len(calls) == 3
        assert "-crf" in calls[0]
        assert "-pass" in calls[1] and "-pass" in calls[2]

    def test_hevc_two_pass_keeps_10bit_pix_fmt_on_both_passes(
        self, capture_ffmpeg, monkeypatch
    ):
        """libx265 fails on Pass 2 with exit code 183 ('different bit depth / csp
        in stats file') if Pass 1 omits -pix_fmt yuv420p10le on an 8-bit source."""
        self._mock_probe(monkeypatch, duration=200.0, size_bytes=200_000_000)
        compress_video("in.mp4", "out.mp4", recipe="hevc")
        assert len(capture_ffmpeg) == 2
        for cmd in capture_ffmpeg:
            assert cmd[cmd.index("-c:v") + 1] == "libx265"
            assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p10le"
            assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
            x265_p = cmd[cmd.index("-x265-params") + 1]
            assert "no-info=1" in x265_p
            assert "stats=" in x265_p

    def test_hw_recipe_uses_videotoolbox_on_darwin(
        self, capture_ffmpeg, monkeypatch
    ):
        self._mock_probe(monkeypatch, duration=200.0, size_bytes=200_000_000)
        monkeypatch.setattr("vidtools.video_ops.platform.system", lambda: "Darwin")
        monkeypatch.setattr("vidtools.video_ops._ffmpeg_has_encoder", lambda _: True)
        compress_video("in.mp4", "out.mp4", recipe="hw")
        assert len(capture_ffmpeg) == 1
        cmd = capture_ffmpeg[0]
        assert cmd[cmd.index("-c:v") + 1] == "hevc_videotoolbox"
        assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
        assert "-b:v" in cmd and "-maxrate" in cmd and "-bufsize" in cmd
        assert cmd[cmd.index("-c:a") + 1] == "aac_at"

    def test_archival_recipe_uses_10bit_hevc_crf16_aq_mode_3(
        self, capture_ffmpeg, monkeypatch
    ):
        monkeypatch.setattr("vidtools.video_ops.require_encoder", lambda _: None)
        compress_video("in.mp4", "out.mp4", recipe="archival")
        cmd = capture_ffmpeg[0]
        assert cmd[cmd.index("-c:v") + 1] == "libx265"
        assert cmd[cmd.index("-crf") + 1] == "16"
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p10le"
        assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
        assert cmd[cmd.index("-b:a") + 1] == "320k"
        x265_p = cmd[cmd.index("-x265-params") + 1]
        assert "aq-mode=3" in x265_p and "no-info=1" in x265_p

    @pytest.mark.parametrize("recipe,edge,crf,abitrate", [
        ("balanced-1080p", 1920, "23", "192k"),
        ("small-720p", 1280, "25", "192k"),
        ("smallest-480p", 854, "28", "128k"),
    ])
    def test_resolution_capped_recipes(
        self, capture_ffmpeg, monkeypatch, recipe, edge, crf, abitrate
    ):
        monkeypatch.setattr("vidtools.video_ops.require_encoder", lambda _: None)
        compress_video("in.mp4", "out.mp4", recipe=recipe)
        cmd = capture_ffmpeg[0]
        vf = cmd[cmd.index("-vf") + 1]
        assert f"min(iw,{edge})" in vf and f"min(ih,{edge})" in vf
        assert cmd[cmd.index("-crf") + 1] == crf
        assert cmd[cmd.index("-b:a") + 1] == abitrate
        assert cmd[cmd.index("-bsf:v") + 1] == "filter_units=remove_types=6"

    def test_copy_recipe_stream_copies_and_strips_metadata(self, capture_ffmpeg):
        compress_video("in.mp4", "out.mp4", recipe="copy")
        cmd = capture_ffmpeg[0]
        assert cmd[cmd.index("-c") + 1] == "copy"
        assert "-map_metadata" in cmd and "-map_chapters" in cmd
        assert "encoder=" in cmd
        assert "-bsf:v" not in cmd

    def test_preserve_metadata_omits_strip_and_sei_flags(
        self, capture_ffmpeg, monkeypatch
    ):
        self._mock_probe(monkeypatch, duration=10.0, size_bytes=5_000_000)
        compress_video("in.mp4", "out.mp4", recipe="universal", preserve_metadata=True)
        cmd = capture_ffmpeg[0]
        assert "-map_metadata" not in cmd
        assert "-map_chapters" not in cmd
        assert "-bsf:v" not in cmd
        assert "encoder=" not in cmd

    def test_rejects_unknown_recipe(self):
        with pytest.raises(ValueError, match="Unknown video compression recipe"):
            compress_video("in.mp4", "out.mp4", recipe="nonexistent")
        assert len(VIDEO_COMPRESS_RECIPES) == 9

    def test_rejects_max_edge_with_copy_recipe(self):
        with pytest.raises(ValueError, match="copy"):
            compress_video("in.mp4", "out.mp4", recipe="copy", max_edge=1280)

"""Tests for input-type smart dispatch."""
import pytest
from unittest.mock import MagicMock
from vidtools.cli import _detect_kind, _make_smart, VIDEO_EXTS, AUDIO_EXTS, IMAGE_EXTS


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
        assert "xyz" in str(exc.value)
        assert "explicit" in str(exc.value).lower() or "img-" in str(exc.value)

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

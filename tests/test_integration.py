"""Real-ffmpeg integration tests.

Unlike the rest of the suite (which mocks `run_ffmpeg`), these invoke the actual
ffmpeg/PIL toolchain against a tiny lavfi-generated clip. They exist because the
mocked tests verify *command shape*, not *ffmpeg behavior* -- and several real
bugs this project hit were exactly that gap (convert->mp3 muxer failure, a
screenshot seek past EOF that exits 0 with no output, a build missing libwebp).

Gating is per-test via the `ffmpeg` marker, not file-wide: the two CLI tests at
the bottom drive the *image* path, which is pure PIL, and must keep running on a
machine with no ffmpeg installed. See tests/conftest.py.
"""

import os
import subprocess

import pytest

from vidtools.operations import (
    convert_format, screenshot, cut_video, resize_video,
)
from vidtools.audio_ops import cut_audio, normalize_audio
from vidtools.progress import FfmpegError

pytestmark = pytest.mark.ffmpeg


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def _stream_types(path: str) -> list[str]:
    """Return the codec_type of each stream (e.g. ['video', 'audio'])."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    ).stdout
    return out.split()


def _audio_codec(path: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    ).stdout.strip()


def _video_width(path: str) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    ).stdout.strip()
    return int(out)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """Generate a 2s A/V clip, a still image, and an audio file once per module."""
    d = tmp_path_factory.mktemp("media")
    clip = d / "clip.mp4"
    # `-g 15` gives a keyframe every 0.5s so stream-copy cuts behave like real
    # footage (a single-keyframe clip can't be copy-cut mid-stream -- expected
    # ffmpeg behavior, but not what we want to assert against here).
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
          "-g", "15", "-pix_fmt", "yuv420p", str(clip)])
    img = d / "img.png"
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240",
          "-frames:v", "1", str(img)])
    audio = d / "audio.mp3"
    _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio)])
    return {"clip": str(clip), "img": str(img), "audio": str(audio)}


class TestConvertReal:
    def test_video_to_mp3_drops_video(self, media, tmp_path):
        """The regression: an audio-only output must not carry a video stream."""
        out = str(tmp_path / "out.mp3")
        convert_format(media["clip"], out, "mp3")
        assert os.path.getsize(out) > 0
        assert _stream_types(out) == ["audio"]

    def test_video_to_wav(self, media, tmp_path):
        out = str(tmp_path / "out.wav")
        convert_format(media["clip"], out, "wav")
        assert _stream_types(out) == ["audio"]

    def test_video_to_gif(self, media, tmp_path):
        out = str(tmp_path / "out.gif")
        convert_format(media["clip"], out, "gif")
        assert os.path.getsize(out) > 0

    def test_video_to_mp3_quality_maps_to_bitrate(self, media, tmp_path):
        """`convert clip.mp4 out.mp3 -q N` must honor --quality (route to the
        audio path), not silently ignore it. Higher quality -> bigger file."""
        from vidtools.cli import parse_arguments
        hi, lo = str(tmp_path / "hi.mp3"), str(tmp_path / "lo.mp3")
        for out, q in ((hi, "95"), (lo, "20")):
            args = parse_arguments(["convert", media["clip"], out, "-q", q])
            args.func(args)
        assert _audio_codec(hi) == "mp3" and _audio_codec(lo) == "mp3"
        assert os.path.getsize(hi) > os.path.getsize(lo)


class TestScreenshotReal:
    def test_png_lossless(self, media, tmp_path):
        out = str(tmp_path / "s.png")
        screenshot(media["clip"], out, timestamp="1")
        from PIL import Image
        with Image.open(out) as im:
            assert im.format == "PNG"

    def test_jpg_via_pil(self, media, tmp_path):
        out = str(tmp_path / "s.jpg")
        screenshot(media["clip"], out, timestamp="1", quality=90)
        from PIL import Image
        with Image.open(out) as im:
            assert im.format == "JPEG"

    def test_webp_via_pil(self, media, tmp_path):
        """Works even though this ffmpeg build lacks libwebp (PIL handles it)."""
        out = str(tmp_path / "s.webp")
        screenshot(media["clip"], out, timestamp="1", quality=80)
        from PIL import Image
        with Image.open(out) as im:
            assert im.format == "WEBP"

    def test_seek_past_end_raises(self, media, tmp_path):
        """ffmpeg exits 0 with no output on an overshoot; we must surface it."""
        out = str(tmp_path / "x.png")
        with pytest.raises(FfmpegError, match="No frame"):
            screenshot(media["clip"], out, timestamp="999")
        assert not os.path.exists(out)


class TestTrimReal:
    def test_cut_video_copy(self, media, tmp_path):
        # Mid-clip stream-copy cut keeps the video stream (keyframed source).
        out = str(tmp_path / "t.mp4")
        cut_video(media["clip"], out, start_time="0.5", end_time="1.5")
        assert "video" in _stream_types(out)

    def test_cut_audio(self, media, tmp_path):
        out = str(tmp_path / "t.mp3")
        cut_audio(media["audio"], out, start_time="0", duration="1")
        assert os.path.getsize(out) > 0


class TestResizeReal:
    def test_resize_half(self, media, tmp_path):
        out = str(tmp_path / "r.mp4")
        resize_video(media["clip"], out, percentage=0.5)
        assert _video_width(out) == 160  # 320 * 0.5


class TestAudioVerbRemuxReal:
    """The CLI `_audio_verb` path: process a video's audio and keep the video."""

    def test_normalize_video_output_keeps_both_streams(self, media, tmp_path):
        from types import SimpleNamespace
        from vidtools.cli import _audio_verb
        out = str(tmp_path / "n.mp4")
        args = SimpleNamespace(input=media["clip"], output=out, target=-16.0)
        handler = _audio_verb(lambda a, i, o: normalize_audio(i, o, target_loudness=a.target))
        handler(args)
        assert set(_stream_types(out)) == {"audio", "video"}
        # Remux must produce a container-friendly codec (aac), not raw PCM.
        assert _audio_codec(out) == "aac"

    def test_normalize_video_to_audio_output_extracts(self, media, tmp_path):
        from types import SimpleNamespace
        from vidtools.cli import _audio_verb
        out = str(tmp_path / "n.mp3")
        args = SimpleNamespace(input=media["clip"], output=out, target=-16.0)
        handler = _audio_verb(lambda a, i, o: normalize_audio(i, o, target_loudness=a.target))
        handler(args)
        assert _stream_types(out) == ["audio"]

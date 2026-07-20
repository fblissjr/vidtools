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
    extract_audio,
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


class TestMetadataPrivacyReal:
    """Real ffmpeg: identifying tags must not ride along into the output.

    Video failed the opposite way from images. Image ops dropped everything
    including data they should have kept; video ops carried everything through,
    so `trim`, `convert` and `resize` all shipped the source's GPS coordinates.
    Only `sanitize` stripped them.
    """

    @pytest.fixture(scope="class")
    def tagged(self, tmp_path_factory):
        d = tmp_path_factory.mktemp("tagged")
        src = str(d / "src.mp4")
        _run(["ffmpeg", "-v", "error", "-f", "lavfi",
              "-i", "testsrc=duration=1:size=160x120:rate=10",
              "-metadata", "location=+37.7749-122.4194/",
              "-metadata", "make=ExampleCorp",
              "-metadata", "comment=a generation prompt that should not travel",
              "-c:v", "libx264", "-y", src])
        return src, d

    def _tag(self, path, key):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", f"format_tags={key}",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True).stdout.strip()
        return out or None

    def test_source_actually_carries_the_tags(self, tagged):
        src, _ = tagged
        assert self._tag(src, "location"), "fixture lost its GPS tag; test proves nothing"

    @pytest.mark.parametrize("op", ["trim", "convert", "resize"])
    def test_identifying_tags_are_stripped_by_default(self, tagged, op):
        src, d = tagged
        out = str(d / f"{op}.mp4")
        {
            "trim": lambda: cut_video(src, out, start_time=0, end_time=1),
            "convert": lambda: convert_format(src, out, "mp4"),
            "resize": lambda: resize_video(src, out, percentage=0.5),
        }[op]()
        for key in ("location", "make", "comment"):
            assert self._tag(out, key) is None, f"{op} leaked '{key}'"

    def test_preserve_metadata_keeps_them(self, tagged):
        src, d = tagged
        out = str(d / "kept.mp4")
        cut_video(src, out, start_time=0, end_time=1, preserve_metadata=True)
        assert self._tag(out, "location") == "+37.7749-122.4194/"

    def test_stripping_does_not_disturb_rotation(self, tmp_path):
        """The reason this was safe to default on.

        A portrait phone video stores a landscape buffer plus a 90-degree
        display matrix. On a stream copy the matrix survives `-map_metadata -1`;
        on a re-encode ffmpeg auto-rotates during decode, so the pixels come out
        upright and the matrix is no longer needed. Either way the video must
        display the same way round.
        """
        plain, rotated = str(tmp_path / "p.mp4"), str(tmp_path / "r.mp4")
        _run(["ffmpeg", "-v", "error", "-f", "lavfi",
              "-i", "testsrc=duration=1:size=320x180:rate=10",
              "-metadata", "location=+37.7749-122.4194/",
              "-c:v", "libx264", "-y", plain])
        _run(["ffmpeg", "-v", "error", "-display_rotation", "90",
              "-i", plain, "-c", "copy", "-y", rotated])

        def displayed(path):
            w, h = [int(v) for v in subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
                capture_output=True, text=True).stdout.strip().rstrip(",").split(",")[:2]]
            rot = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream_side_data=rotation", "-of", "default=nw=1:nk=1", path],
                capture_output=True, text=True).stdout.strip().splitlines()
            if rot and abs(int(float(rot[0]))) == 90:
                w, h = h, w
            return w, h

        before = displayed(rotated)
        assert before[1] > before[0], "fixture is not portrait; test proves nothing"

        for label, out, run in [
            ("copy", str(tmp_path / "c.mp4"),
             lambda o: cut_video(rotated, o, start_time=0, end_time=1)),
            ("re-encode", str(tmp_path / "e.mp4"),
             lambda o: resize_video(rotated, o, percentage=1.0)),
        ]:
            run(out)
            assert self._tag(out, "location") is None, f"{label} leaked GPS"
            assert displayed(out) == before, (
                f"{label} changed the displayed orientation: {before} -> {displayed(out)}"
            )


@pytest.mark.ffmpeg
def test_extract_audio_strips_location_keeps_title(tmp_path):
    """Real ffmpeg: the audio policy must survive an actual mux.

    Command-shape tests prove the flags are emitted; only running ffmpeg proves
    the tags are actually absent from the container afterwards.
    """
    src = tmp_path / "src.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-metadata", "location=+37.7749-122.4194/",
        "-metadata", "comment=SECRET",
        "-metadata", "title=Keep This",
        str(src)], check=True, capture_output=True)

    out = tmp_path / "out.m4a"
    extract_audio(str(src), str(out), audio_format="aac")
    tags = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "default", str(out)],
        capture_output=True, text=True).stdout.lower()
    assert "secret" not in tags
    assert "37.7749" not in tags
    assert "keep this" in tags, "descriptive tags must survive the audio policy"

    kept = tmp_path / "kept.m4a"
    extract_audio(str(src), str(kept), audio_format="aac", preserve_metadata=True)
    kept_tags = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "default", str(kept)],
        capture_output=True, text=True).stdout.lower()
    assert "37.7749" in kept_tags and "secret" in kept_tags

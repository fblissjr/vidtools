"""get_audio_info and the encoder capability guard.

These run against real ffmpeg rather than a mock: a mock can assert any string
shape, including one ffmpeg never emits.
"""

import subprocess

import pytest

from vidtools.operations import get_audio_info

pytestmark = pytest.mark.ffmpeg


def _audio(tmp_path, filtergraph, name="a.wav"):
    p = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", filtergraph, str(p)],
                   check=True, capture_output=True)
    return p


class TestGetAudioInfo:
    def test_reports_stream_and_format(self, tmp_path):
        src = _audio(tmp_path, "sine=f=440:d=1")
        info = get_audio_info(str(src))
        assert info["streams"], "no audio stream reported"
        assert info["streams"][0]["codec_type"] == "audio"
        assert float(info["format"]["duration"]) == pytest.approx(1.0, abs=0.2)

    def test_selects_only_audio_streams_from_a_video(self, tmp_path):
        """-select_streams a must exclude video, or callers reading streams[0]
        get the video track's fields."""
        v = tmp_path / "v.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=d=1:s=160x120:r=10",
            "-f", "lavfi", "-i", "sine=f=440:d=1", "-shortest", str(v)],
            check=True, capture_output=True)
        info = get_audio_info(str(v))
        assert all(s["codec_type"] == "audio" for s in info["streams"])

    def test_unreadable_input_raises_ffmpeg_error(self, tmp_path):
        """`pytest.raises(Exception)` accepted the raw `CalledProcessError`
        that `main()` does not catch -- so `vidtools info broken.mp3` crashed
        with a traceback while this test stayed green."""
        from vidtools.progress import FfmpegError
        broken = tmp_path / "broken.wav"
        broken.write_bytes(b"not audio")
        with pytest.raises(FfmpegError, match="broken.wav"):
            get_audio_info(str(broken))


class TestEncoderCapabilityGuard:
    """Homebrew's slim ffmpeg omits libaom-av1 and libvorbis, so `.ogg` output
    and `--video-codec libaom-av1` fail on a default macOS install. They must name
    the gap rather than emit ffmpeg's 'Unknown encoder'."""

    def test_missing_encoder_names_the_gap(self):
        from vidtools import utils
        from vidtools.progress import FfmpegError
        import unittest.mock as m
        with m.patch.object(utils, "_ffmpeg_has_encoder", lambda n: False):
            with pytest.raises(FfmpegError, match="libvorbis"):
                utils.require_encoder("libvorbis")

    def test_present_encoder_passes(self):
        from vidtools import utils
        import unittest.mock as m
        with m.patch.object(utils, "_ffmpeg_has_encoder", lambda n: True):
            utils.require_encoder("libvorbis")  # must not raise

    def test_unprobeable_build_does_not_block(self):
        """None means 'could not ask'. Blocking there would turn a missing
        ffmpeg into a bogus 'missing codec' report."""
        from vidtools import utils
        import unittest.mock as m
        with m.patch.object(utils, "_ffmpeg_has_encoder", lambda n: None):
            utils.require_encoder("libvorbis")  # must not raise

    def test_encoders_and_filters_share_one_probe(self):
        """Two capability kinds, one implementation -- three hand-rolled
        subprocess probes would drift apart."""
        from vidtools import utils
        utils._CAPABILITY_CACHE.clear()
        assert "libx264" in (utils._ffmpeg_capabilities("encoders") or set())
        assert "scale" in (utils._ffmpeg_capabilities("filters") or set())
        assert set(utils._CAPABILITY_CACHE) == {"encoders", "filters"}

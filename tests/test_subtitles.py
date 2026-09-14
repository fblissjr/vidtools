"""Subtitle generation and burn-in.

Both ops are CLI-exposed and had no tests. They sit in the two failure classes
this project keeps hitting: timecode arithmetic that produces a plausible file
no player will load, and hand-rolled path escaping of the kind that broke the
Finder Quick Action on apostrophes.
"""

import subprocess

import pytest

from vidtools.operations import add_subtitles, create_srt_from_transcript
from vidtools.video_ops import _escape_subtitle_path


def _write(tmp_path, body):
    p = tmp_path / "transcript.txt"
    p.write_text(body)
    return p


class TestSrtFromTranscript:
    def test_basic_conversion(self, tmp_path):
        src = _write(tmp_path, "[0.0 --> 2.5] Hello there\n[2.5 --> 5.0] Second line\n")
        out = tmp_path / "out.srt"
        create_srt_from_transcript(str(src), str(out))
        assert out.read_text() == (
            "1\n00:00:00,000 --> 00:00:02,500\nHello there\n\n"
            "2\n00:00:02,500 --> 00:00:05,000\nSecond line\n\n"
        )

    def test_milliseconds_are_not_truncated_to_zero(self, tmp_path):
        """A separator of '.' instead of ',' or dropped ms yields a file that
        looks right and that no player will load."""
        src = _write(tmp_path, "[1.234 --> 2.567] x\n")
        out = tmp_path / "o.srt"
        create_srt_from_transcript(str(src), str(out))
        text = out.read_text()
        assert "00:00:01,234 --> 00:00:02,567" in text
        assert "." not in text.split("\n")[1], "SRT requires a comma before ms"

    def test_hours_and_minutes(self, tmp_path):
        src = _write(tmp_path, "[3661.5 --> 3725.0] later\n")
        out = tmp_path / "o.srt"
        create_srt_from_transcript(str(src), str(out))
        assert "01:01:01,500 --> 01:02:05,000" in out.read_text()

    def test_beyond_24_hours_does_not_wrap(self, tmp_path):
        """timedelta.seconds caps at 86399 and rolls the remainder into .days,
        so a naive implementation renders hour 25 as hour 1."""
        src = _write(tmp_path, "[90061.0 --> 90062.0] long\n")  # 25:01:01
        out = tmp_path / "o.srt"
        create_srt_from_transcript(str(src), str(out))
        assert "25:01:01,000" in out.read_text(), out.read_text()

    def test_lines_without_timestamps_are_skipped(self, tmp_path):
        src = _write(tmp_path, "preamble\n[0.0 --> 1.0] kept\ntrailing junk\n")
        out = tmp_path / "o.srt"
        create_srt_from_transcript(str(src), str(out))
        assert out.read_text().count("-->") == 1
        assert "kept" in out.read_text()

    def test_integer_timestamps_accepted(self, tmp_path):
        src = _write(tmp_path, "[5 --> 7] int form\n")
        out = tmp_path / "o.srt"
        create_srt_from_transcript(str(src), str(out))
        assert "00:00:05,000 --> 00:00:07,000" in out.read_text()

    def test_empty_transcript_produces_empty_srt(self, tmp_path):
        src = _write(tmp_path, "nothing here\n")
        out = tmp_path / "o.srt"
        create_srt_from_transcript(str(src), str(out))
        assert out.read_text() == ""


class TestEscapeSubtitlePath:
    @pytest.mark.parametrize("raw", [
        "plain.srt",
        "with space.srt",
        "it's.srt",
        "sub:colon.srt",
        "back\\slash.srt",
    ])
    def test_escaping_is_reversible_enough_to_survive(self, raw):
        esc = _escape_subtitle_path(raw)
        # every special char that ffmpeg's filter grammar treats specially must
        # come back escaped, not dropped
        for ch in (":", "'", "\\"):
            if ch in raw:
                assert f"\\{ch}" in esc, f"{ch!r} not escaped in {esc!r}"

    def test_backslash_escaped_before_others(self):
        """Order matters: escaping ':' first then '\\' would double-escape the
        backslash introduced by the colon."""
        assert _escape_subtitle_path("a:b") == "a\\:b"
        assert _escape_subtitle_path("a\\b") == "a\\\\b"


def _has_subtitles_filter():
    from vidtools.video_ops import _ffmpeg_has_filter
    try:
        return _ffmpeg_has_filter("subtitles")
    except Exception:
        return False


@pytest.mark.ffmpeg
def test_missing_libass_gives_an_actionable_error(tmp_path):
    """The capability gap must name itself. Without this the user gets an
    'Error parsing filterchain' with no indication that their ffmpeg simply
    lacks libass."""
    import unittest.mock as m

    from vidtools import video_ops
    from vidtools.progress import FfmpegError
    with m.patch.object(video_ops, "_ffmpeg_has_filter", lambda n: False):
        with pytest.raises(FfmpegError, match="libass"):
            video_ops.add_subtitles("in.mp4", "out.mp4", "s.srt")


@pytest.mark.ffmpeg
def test_unprobeable_ffmpeg_does_not_blame_libass(tmp_path):
    """A failed probe (ffmpeg absent, or timed out) returns None. Treating None
    as "filter missing" would tell someone with no ffmpeg to install libass."""
    import unittest.mock as m

    from vidtools import video_ops
    with m.patch.object(video_ops, "_ffmpeg_has_filter", lambda n: None):
        with m.patch.object(video_ops, "run_ffmpeg") as run:
            video_ops.add_subtitles("in.mp4", "out.mp4", "s.srt")
    assert run.called, "a failed probe must not block the attempt"


@pytest.mark.ffmpeg
def test_failed_probe_is_not_cached(tmp_path):
    """An lru_cache would pin a transient timeout for the process lifetime."""
    import unittest.mock as m

    from vidtools import utils
    utils._CAPABILITY_CACHE.clear()
    with m.patch.object(utils.subprocess, "run", side_effect=OSError("boom")):
        assert utils._ffmpeg_capabilities("filters") is None
    assert not utils._CAPABILITY_CACHE, "failure was cached"
    assert utils._ffmpeg_capabilities("filters") is not None, "probe did not retry"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not _has_subtitles_filter(),
                    reason="ffmpeg built without libass; no subtitles filter")
class TestAddSubtitlesReal:
    """add_subtitles hand-escapes the path into ffmpeg's filter grammar. A
    mocked test proves the string was assembled; only ffmpeg proves it parses.
    That gap is where the Quick Action's apostrophe bugs lived."""

    def _clip(self, tmp_path, name="v.mp4"):
        p = tmp_path / name
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "testsrc=duration=1:size=160x120:rate=10", str(p)],
            check=True, capture_output=True)
        return p

    def _srt(self, tmp_path, name="s.srt"):
        p = tmp_path / name
        p.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n")
        return p

    def test_burns_in_subtitles(self, tmp_path):
        out = tmp_path / "out.mp4"
        add_subtitles(str(self._clip(tmp_path)), str(out), str(self._srt(tmp_path)))
        assert out.exists() and out.stat().st_size > 0

    @pytest.mark.parametrize("srt_name", [
        "it's a sub.srt",
        "with space.srt",
        "bracket[1].srt",
    ])
    def test_awkward_subtitle_paths_parse(self, tmp_path, srt_name):
        """ffmpeg's filter grammar treats ':' and quotes as syntax. If the
        escaping is wrong the filter fails to parse and no subtitles appear --
        which a command-shape assertion cannot detect."""
        out = tmp_path / "out.mp4"
        add_subtitles(str(self._clip(tmp_path)), str(out),
                      str(self._srt(tmp_path, srt_name)))
        assert out.exists() and out.stat().st_size > 0

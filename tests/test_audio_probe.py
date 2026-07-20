"""detect_silence and get_audio_info -- both were untested.

detect_silence parses ffmpeg's `silencedetect` stderr, the same shape as the
loudnorm regex that turned out to be exercised only on its fallback branch.
So these run against real ffmpeg rather than a mock: a mock can assert any
string shape, including one ffmpeg never emits.
"""

import subprocess

import pytest

from vidtools.operations import detect_silence, get_audio_info

pytestmark = pytest.mark.ffmpeg


def _audio(tmp_path, filtergraph, name="a.wav"):
    p = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", filtergraph, str(p)],
                   check=True, capture_output=True)
    return p


class TestDetectSilence:
    def test_finds_silence_between_tones(self, tmp_path):
        """tone(1s) - silence(2s) - tone(1s)."""
        src = _audio(tmp_path, "sine=f=440:d=1")
        quiet = _audio(tmp_path, "anullsrc=r=44100:cl=mono:d=2", "q.wav")
        joined = tmp_path / "joined.wav"
        lst = tmp_path / "l.txt"
        lst.write_text(f"file '{src}'\nfile '{quiet}'\nfile '{src}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), str(joined)], check=True, capture_output=True)

        ranges = detect_silence(str(joined), threshold=-30, min_duration=0.5)
        assert ranges, "no silence detected in a clip with 2s of silence"
        start, end = ranges[0]
        assert 0.8 < start < 1.3, f"silence started at {start}, expected ~1.0"
        assert 2.7 < end < 3.3, f"silence ended at {end}, expected ~3.0"

    def test_no_silence_in_continuous_tone(self, tmp_path):
        src = _audio(tmp_path, "sine=f=440:d=2")
        assert detect_silence(str(src), threshold=-30, min_duration=0.5) == []

    def test_trailing_silence_to_end_of_file(self, tmp_path):
        """Guards a suspected truncation that did not reproduce.

        `detect_silence` zips starts against ends, so an unmatched trailing
        `silence_start` would be dropped. Real ffmpeg turns out to emit
        `silence_end` at EOF (verified: start 0.999909, end 3), so the pairing
        holds. Kept because the zip is only safe while that stays true, and
        nothing else would notice if it changed."""
        src = _audio(tmp_path, "sine=f=440:d=1")
        quiet = _audio(tmp_path, "anullsrc=r=44100:cl=mono:d=2", "q.wav")
        joined = tmp_path / "trail.wav"
        lst = tmp_path / "l2.txt"
        lst.write_text(f"file '{src}'\nfile '{quiet}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), str(joined)], check=True, capture_output=True)

        ranges = detect_silence(str(joined), threshold=-30, min_duration=0.5)
        assert ranges, "trailing silence was dropped"
        assert ranges[-1][1] > ranges[-1][0], "range must be ordered"

    def test_ranges_are_ordered_pairs(self, tmp_path):
        src = _audio(tmp_path, "sine=f=440:d=1")
        quiet = _audio(tmp_path, "anullsrc=r=44100:cl=mono:d=1", "q.wav")
        joined = tmp_path / "j.wav"
        lst = tmp_path / "l3.txt"
        lst.write_text(f"file '{src}'\nfile '{quiet}'\nfile '{src}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), str(joined)], check=True, capture_output=True)
        for start, end in detect_silence(str(joined), min_duration=0.3):
            assert end > start


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

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(Exception):
            get_audio_info(str(tmp_path / "nope.wav"))

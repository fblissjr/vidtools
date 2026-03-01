"""Tests for vidtools.ytdlp module."""

import pytest

from vidtools.ytdlp import (
    parse_ytdlp_progress,
    build_ytdlp_command,
    validate_download_params,
)


class TestParseYtdlpProgress:
    def test_parse_download_percent(self):
        line = "[download]  45.2% of  12.34MiB at  2.50MiB/s ETA 00:03"
        result = parse_ytdlp_progress(line)
        assert result is not None
        assert result["percent"] == 45
        assert "speed" in result

    def test_parse_100_percent(self):
        line = "[download] 100% of  12.34MiB in 00:05"
        result = parse_ytdlp_progress(line)
        assert result is not None
        assert result["percent"] == 100

    def test_parse_non_progress_line(self):
        line = "[youtube] Extracting video information..."
        result = parse_ytdlp_progress(line)
        assert result is None

    def test_parse_empty_line(self):
        result = parse_ytdlp_progress("")
        assert result is None

    def test_parse_destination_line(self):
        line = "[download] Destination: My Video-abc123.mp4"
        result = parse_ytdlp_progress(line)
        assert result is None


class TestBuildYtdlpCommand:
    def test_mp3_download(self):
        cmd = build_ytdlp_command(
            url="https://www.youtube.com/watch?v=test123",
            output_path="/tmp/output.mp3",
            format_type="mp3",
        )
        assert cmd[0] == "yt-dlp"
        assert "--extract-audio" in cmd
        assert "--audio-format" in cmd
        assert "mp3" in cmd
        assert "https://www.youtube.com/watch?v=test123" in cmd

    def test_wav_download(self):
        cmd = build_ytdlp_command(
            url="https://www.youtube.com/watch?v=test123",
            output_path="/tmp/output.wav",
            format_type="wav",
        )
        assert "--extract-audio" in cmd
        assert "wav" in cmd

    def test_mp4_download(self):
        cmd = build_ytdlp_command(
            url="https://www.youtube.com/watch?v=test123",
            output_path="/tmp/output.mp4",
            format_type="mp4",
        )
        assert "--extract-audio" not in cmd
        assert "--merge-output-format" in cmd
        assert "mp4" in cmd

    def test_output_template(self):
        cmd = build_ytdlp_command(
            url="https://www.youtube.com/watch?v=test123",
            output_path="/data/output/video.mp4",
            format_type="mp4",
        )
        assert "-o" in cmd
        idx = cmd.index("-o")
        assert cmd[idx + 1] == "/data/output/video.mp4"

    def test_no_playlist(self):
        cmd = build_ytdlp_command(
            url="https://www.youtube.com/watch?v=test123",
            output_path="/tmp/out.mp3",
            format_type="mp3",
        )
        assert "--no-playlist" in cmd

    def test_max_duration(self):
        cmd = build_ytdlp_command(
            url="https://www.youtube.com/watch?v=test123",
            output_path="/tmp/out.mp3",
            format_type="mp3",
            max_duration=3600,
        )
        assert "--match-filter" in cmd
        idx = cmd.index("--match-filter")
        assert "duration" in cmd[idx + 1]


class TestValidateDownloadParams:
    def test_valid_mp3(self):
        errors = validate_download_params("mp3")
        assert errors == []

    def test_valid_wav(self):
        assert validate_download_params("wav") == []

    def test_valid_mp4(self):
        assert validate_download_params("mp4") == []

    def test_invalid_format(self):
        errors = validate_download_params("exe")
        assert len(errors) > 0
        assert "format" in errors[0].lower()

"""Tests for vidtools.ytdlp module."""

from vidtools.ytdlp import (
    build_ytdlp_command,
    build_ytdlp_live_command,
    parse_ytdlp_live_progress,
    parse_ytdlp_progress,
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

    def test_print_filepath_flag(self):
        """Verify --print after_move:filepath is included for reliable output detection."""
        cmd = build_ytdlp_command(
            url="https://www.youtube.com/watch?v=test123",
            output_path="/tmp/out.mp3",
            format_type="mp3",
        )
        assert "--print" in cmd
        idx = cmd.index("--print")
        assert cmd[idx + 1] == "after_move:filepath"

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


class TestBuildYtdlpLiveCommand:
    URL = "https://www.youtube.com/@channel/live"

    def test_default_flags(self):
        cmd = build_ytdlp_live_command(self.URL, "/tmp/show.mp4")
        for flag in (
            "--no-part", "--live-from-start",
            "--retries", "--fragment-retries",
        ):
            assert flag in cmd, f"missing {flag}"
        assert cmd[cmd.index("--retries") + 1] == "infinite"
        assert cmd[cmd.index("--fragment-retries") + 1] == "infinite"

    def test_always_remux_mp4(self):
        """Regression: the long-URL/ARG_MAX workaround requires mp4 output
        and `--remux-video mp4` for clean final output. Both must be present."""
        cmd = build_ytdlp_live_command(self.URL, "/tmp/show.mp4")
        assert "--remux-video" in cmd
        assert cmd[cmd.index("--remux-video") + 1] == "mp4"

    def test_no_hls_use_mpegts(self):
        """Regression: `--hls-use-mpegts` triggers OSError Errno 63 on macOS
        for YouTube HLS streams (long signed manifest URL overflows ARG_MAX)."""
        cmd = build_ytdlp_live_command(self.URL, "/tmp/show.mp4")
        assert "--hls-use-mpegts" not in cmd

    def test_wait_interval(self):
        cmd = build_ytdlp_live_command(self.URL, "/tmp/x.mp4", wait_interval=30)
        assert "--wait-for-video" in cmd
        assert cmd[cmd.index("--wait-for-video") + 1] == "30"

    def test_no_wait(self):
        cmd = build_ytdlp_live_command(self.URL, "/tmp/x.mp4", wait_interval=0)
        assert "--wait-for-video" not in cmd

    def test_from_now(self):
        cmd = build_ytdlp_live_command(self.URL, "/tmp/x.mp4", from_start=False)
        assert "--live-from-start" not in cmd

    def test_no_duration_filter(self):
        """Live streams have unknown duration; the duration<= filter must not be added."""
        cmd = build_ytdlp_live_command(self.URL, "/tmp/x.mp4")
        assert "--match-filter" not in cmd

    def test_url_is_last(self):
        cmd = build_ytdlp_live_command(self.URL, "/tmp/x.mp4")
        assert cmd[-1] == self.URL


class TestParseYtdlpLiveProgress:
    def test_waiting(self):
        result = parse_ytdlp_live_progress("[wait] Waiting for video to be available")
        assert result is not None
        assert "wait" in result["message"].lower()

    def test_fragment(self):
        result = parse_ytdlp_live_progress("[download] Downloading fragment 42")
        assert result is not None
        assert "42" in result["message"]
        assert result["percent"] < 100  # never report complete mid-stream

    def test_fragment_percent_is_monotonic(self):
        """Regression: fragment 150 must not wrap back to 50 (was `frag % 100`).
        Wrapping makes the CLI bar stall and the web UI jump backward."""
        low = parse_ytdlp_live_progress("[download] Downloading fragment 50")
        high = parse_ytdlp_live_progress("[download] Downloading fragment 150")
        assert low is not None and high is not None
        assert high["percent"] >= low["percent"]
        assert high["percent"] == 99  # capped, not wrapped

    def test_hls_segment(self):
        line = "[hls @ 0x140] Opening 'https://example.com/path/seg-789-foo.ts' for reading"
        result = parse_ytdlp_live_progress(line)
        assert result is not None
        assert "789" in result["message"]

    def test_unrelated_line(self):
        assert parse_ytdlp_live_progress("[youtube] Extracting video info") is None

    def test_empty(self):
        assert parse_ytdlp_live_progress("") is None


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

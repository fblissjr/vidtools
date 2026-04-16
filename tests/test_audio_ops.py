"""Tests for audio operations."""
from unittest.mock import patch, MagicMock
from vidtools.audio_ops import (
    convert_audio, cut_audio, normalize_audio, mix_audio,
    fade_audio, resample_audio,
    AUDIO_CODEC_MAP,
)


class TestConvertAudio:
    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_mp3_conversion(self, mock_run):
        convert_audio("input.wav", "output.mp3")
        cmd = mock_run.call_args[0][0]
        assert "-vn" in cmd
        assert "-c:a" in cmd
        idx = cmd.index("-c:a")
        assert cmd[idx + 1] == "libmp3lame"

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_flac_conversion(self, mock_run):
        convert_audio("input.wav", "output.flac")
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("-c:a")
        assert cmd[idx + 1] == "flac"

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_explicit_codec_overrides_auto(self, mock_run):
        convert_audio("input.wav", "output.mp3", audio_codec="aac")
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("-c:a")
        assert cmd[idx + 1] == "aac"

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_bitrate_applied(self, mock_run):
        convert_audio("input.wav", "output.mp3", bitrate="320k")
        cmd = mock_run.call_args[0][0]
        assert "-b:a" in cmd
        idx = cmd.index("-b:a")
        assert cmd[idx + 1] == "320k"

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_sample_rate_applied(self, mock_run):
        convert_audio("input.wav", "output.mp3", sample_rate=22050)
        cmd = mock_run.call_args[0][0]
        assert "-ar" in cmd
        idx = cmd.index("-ar")
        assert cmd[idx + 1] == "22050"


class TestCutAudio:
    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_cut_with_copy(self, mock_run):
        cut_audio("input.mp3", "output.mp3", start_time="00:01:00", end_time="00:02:00")
        cmd = mock_run.call_args[0][0]
        assert "-ss" in cmd
        assert "-to" in cmd
        assert "-c" in cmd
        idx = cmd.index("-c")
        assert cmd[idx + 1] == "copy"

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_cut_with_duration(self, mock_run):
        cut_audio("input.mp3", "output.mp3", start_time="60", duration="30")
        cmd = mock_run.call_args[0][0]
        assert "-t" in cmd


class TestNormalizeAudio:
    @patch("vidtools.audio_ops.subprocess")
    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_normalize_builds_loudnorm(self, mock_run, mock_subprocess):
        # Mock the first pass (analysis)
        mock_result = MagicMock()
        mock_result.stderr = (
            'measured_I=-25.0\nmeasured_TP=-1.0\nmeasured_LRA=10.0\n'
            'measured_thresh=-35.0\n'
        )
        mock_subprocess.run.return_value = mock_result

        normalize_audio("input.mp3", "output.mp3", target_loudness=-16)
        # Should have called run_ffmpeg_command for pass 2
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert any("loudnorm" in str(arg) for arg in cmd)


class TestFadeAudio:
    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_fade_in(self, mock_run):
        fade_audio("input.mp3", "output.mp3", fade_in=3.0)
        cmd = mock_run.call_args[0][0]
        assert "-af" in cmd
        idx = cmd.index("-af")
        assert "afade=t=in" in cmd[idx + 1]

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_fade_out(self, mock_run):
        fade_audio("input.mp3", "output.mp3", fade_out=3.0, total_duration=60.0)
        cmd = mock_run.call_args[0][0]
        idx = cmd.index("-af")
        assert "afade=t=out" in cmd[idx + 1]


class TestResampleAudio:
    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_resample(self, mock_run):
        resample_audio("input.mp3", "output.mp3", sample_rate=22050)
        cmd = mock_run.call_args[0][0]
        assert "-ar" in cmd
        idx = cmd.index("-ar")
        assert cmd[idx + 1] == "22050"


class TestMixAudio:
    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_mix_two_files(self, mock_run):
        mix_audio(["a.mp3", "b.mp3"], "output.mp3")
        cmd = mock_run.call_args[0][0]
        assert "-filter_complex" in cmd
        idx = cmd.index("-filter_complex")
        assert "amix" in cmd[idx + 1]


class TestCodecMap:
    def test_known_formats(self):
        assert AUDIO_CODEC_MAP["mp3"] == "libmp3lame"
        assert AUDIO_CODEC_MAP["flac"] == "flac"
        assert AUDIO_CODEC_MAP["ogg"] == "libvorbis"
        assert AUDIO_CODEC_MAP["wav"] == "pcm_s16le"
        assert AUDIO_CODEC_MAP["aac"] == "aac"

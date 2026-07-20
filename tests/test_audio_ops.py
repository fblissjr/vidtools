"""Tests for audio operations."""
import pytest
from unittest.mock import patch, MagicMock
from vidtools.audio_ops import (
    convert_audio, cut_audio, normalize_audio, mix_audio,
    fade_audio, resample_audio, clean_audio,
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
        # Real `print_format=summary` output. The previous mock used
        # `measured_I=-25.0`, a shape ffmpeg never emits, so the regex never
        # matched and this test silently exercised the single-pass fallback for
        # its entire life while being named for the two-pass path.
        mock_result.stderr = (
            "[Parsed_loudnorm_0 @ 0x600000] \n"
            "Input Integrated:    -25.0 LUFS\n"
            "Input True Peak:      -1.0 dBTP\n"
            "Input LRA:            10.0 LU\n"
            "Input Threshold:     -35.0 LUFS\n"
        )
        mock_subprocess.run.return_value = mock_result

        normalize_audio("input.mp3", "output.mp3", target_loudness=-16)
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        chain = " ".join(str(a) for a in cmd)

        # Asserting only `"loudnorm" in cmd` could not tell the two-pass branch
        # from the single-pass fallback -- both emit the filter. What makes this
        # two-pass is that pass 1's *measurements* reach pass 2.
        assert "measured_I=-25.0" in chain
        assert "measured_TP=-1.0" in chain
        assert "measured_LRA=10.0" in chain
        assert "measured_thresh=-35.0" in chain
        assert "linear=true" in chain
        assert "I=-16" in chain, "target loudness not applied"

    @patch("vidtools.audio_ops.run_ffmpeg")
    @patch("vidtools.audio_ops.subprocess")
    def test_normalize_falls_back_to_single_pass_when_analysis_unparseable(
        self, mock_subprocess, mock_run
    ):
        """The other branch, which nothing covered. Unparseable pass-1 output
        must still normalize -- via single-pass -- rather than crash or silently
        emit a filter carrying no measurements."""
        mock_result = MagicMock()
        mock_result.stderr = "ffmpeg produced nothing parseable here"
        mock_subprocess.run.return_value = mock_result

        normalize_audio("input.mp3", "output.mp3", target_loudness=-16)
        chain = " ".join(str(a) for a in mock_run.call_args[0][0])
        assert "loudnorm" in chain
        assert "measured_I" not in chain, "fallback must not fake measurements"
        assert "I=-16" in chain


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


class TestCleanAudio:
    """clean_audio chains highpass + afftdn + acompressor + loudnorm."""

    def _filter_chain(self, cmd):
        idx = cmd.index("-af")
        return cmd[idx + 1]

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_default_chain_has_all_filters(self, mock_run):
        clean_audio("in.mp3", "out.mp3")
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "highpass=f=80" in chain
        assert "afftdn=nf=-25" in chain
        assert "acompressor=" in chain
        assert "loudnorm=" in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_custom_highpass(self, mock_run):
        clean_audio("in.mp3", "out.mp3", highpass=60)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "highpass=f=60" in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_highpass_disabled(self, mock_run):
        clean_audio("in.mp3", "out.mp3", highpass=None)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "highpass" not in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_denoise_disabled(self, mock_run):
        clean_audio("in.mp3", "out.mp3", denoise=None)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "afftdn" not in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_custom_denoise(self, mock_run):
        clean_audio("in.mp3", "out.mp3", denoise=-30)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "afftdn=nf=-30" in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_compressor_disabled(self, mock_run):
        clean_audio("in.mp3", "out.mp3", compress=False)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "acompressor" not in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_compressor_params_applied(self, mock_run):
        clean_audio("in.mp3", "out.mp3",
                    compress_threshold=-20, compress_ratio=4,
                    compress_attack=10, compress_release=100)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "threshold=-20dB" in chain
        assert "ratio=4" in chain
        assert "attack=10" in chain
        assert "release=100" in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_loudnorm_disabled(self, mock_run):
        clean_audio("in.mp3", "out.mp3", loudnorm=False)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "loudnorm" not in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_loudnorm_targets(self, mock_run):
        clean_audio("in.mp3", "out.mp3", target_lufs=-16, true_peak=-2.0, lra=7)
        chain = self._filter_chain(mock_run.call_args[0][0])
        assert "I=-16" in chain
        assert "TP=-2.0" in chain
        assert "LRA=7" in chain

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_mp3_default_quality_vbr(self, mock_run):
        """mp3 output without explicit bitrate/quality should default to -q:a 2 (VBR ~190k)."""
        clean_audio("in.wav", "out.mp3")
        cmd = mock_run.call_args[0][0]
        assert "-c:a" in cmd
        ca = cmd.index("-c:a")
        assert cmd[ca + 1] == "libmp3lame"
        assert "-q:a" in cmd
        qa = cmd.index("-q:a")
        assert cmd[qa + 1] == "2"

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_explicit_bitrate_overrides_quality(self, mock_run):
        clean_audio("in.wav", "out.mp3", bitrate="320k")
        cmd = mock_run.call_args[0][0]
        assert "-b:a" in cmd
        assert "-q:a" not in cmd

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_wav_output_no_quality_flag(self, mock_run):
        """PCM has no notion of quality; -q:a must not appear for wav."""
        clean_audio("in.mp3", "out.wav")
        cmd = mock_run.call_args[0][0]
        assert "-q:a" not in cmd
        ca = cmd.index("-c:a")
        assert cmd[ca + 1] == "pcm_s16le"

    @patch("vidtools.audio_ops.run_ffmpeg")
    def test_strips_video_stream(self, mock_run):
        """Audio-only output must use -vn so input video tracks are dropped."""
        clean_audio("in.mp4", "out.mp3")
        cmd = mock_run.call_args[0][0]
        assert "-vn" in cmd

    def test_all_filters_disabled_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            clean_audio("in.mp3", "out.mp3",
                        highpass=None, denoise=None, compress=False, loudnorm=False)


class TestCodecMap:
    def test_known_formats(self):
        assert AUDIO_CODEC_MAP["mp3"] == "libmp3lame"
        assert AUDIO_CODEC_MAP["flac"] == "flac"
        assert AUDIO_CODEC_MAP["ogg"] == "libvorbis"
        assert AUDIO_CODEC_MAP["wav"] == "pcm_s16le"
        assert AUDIO_CODEC_MAP["aac"] == "aac"


class TestMetadataPolicy:
    """Audio's policy deliberately differs from image and video.

    Those strip everything by default because their metadata is incidental.
    Audio metadata is frequently the point -- artist, title and album are what
    make a library work -- so `convert song.mp3 song.m4a` silently returning an
    untitled file would be a worse bug than the leak. The kept and dropped sets
    are therefore inverted relative to video, using the same three-category
    model.
    """

    OPS = ["convert_audio", "cut_audio", "normalize_audio", "fade_audio",
           "resample_audio", "clean_audio", "remove_silence"]

    def _call(self, name, kw):
        from vidtools import audio_ops
        return {
            "convert_audio": lambda: audio_ops.convert_audio("i.mp3", "o.mp3", **kw),
            "cut_audio": lambda: audio_ops.cut_audio("i.mp3", "o.mp3", start_time="0", **kw),
            "normalize_audio": lambda: audio_ops.normalize_audio("i.mp3", "o.mp3", **kw),
            "fade_audio": lambda: audio_ops.fade_audio("i.mp3", "o.mp3", fade_in=1, **kw),
            "resample_audio": lambda: audio_ops.resample_audio("i.mp3", "o.mp3", **kw),
            "clean_audio": lambda: audio_ops.clean_audio("i.mp3", "o.mp3", **kw),
            "remove_silence": lambda: audio_ops.remove_silence("i.mp3", "o.mp3", **kw),
        }[name]()

    @pytest.mark.parametrize("op", OPS)
    @patch("vidtools.audio_ops.run_ffmpeg")
    @patch("vidtools.audio_ops.subprocess")
    def test_drops_freeform_tags_by_default(self, mock_sub, mock_run, op):
        mock_sub.run.return_value = MagicMock(stderr="")
        self._call(op, {})
        cmd = mock_run.call_args[0][0]
        assert "comment=" in cmd, f"{op} does not drop the comment tag"
        assert cmd[cmd.index("-map_metadata") + 1] == "0", f"{op} does not keep descriptive tags"

    @pytest.mark.parametrize("op", OPS)
    @patch("vidtools.audio_ops.run_ffmpeg")
    @patch("vidtools.audio_ops.subprocess")
    def test_preserve_metadata_keeps_everything(self, mock_sub, mock_run, op):
        mock_sub.run.return_value = MagicMock(stderr="")
        self._call(op, {"preserve_metadata": True})
        cmd = mock_run.call_args[0][0]
        assert "comment=" not in cmd, f"{op} still filters with preserve_metadata=True"

"""Tests for the operation pipeline."""
from unittest.mock import patch

import pytest

from vidtools.pipeline import Pipeline, PipelineStep


class TestPipelineStep:
    def test_construction(self):
        step = PipelineStep(name="resize", params={"percentage": 0.5})
        assert step.name == "resize"
        assert step.params == {"percentage": 0.5}


class TestPipelineValidation:
    def test_unknown_op_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            Pipeline("in.mp4", "out.mp4", [PipelineStep("nope", {})])

    def test_excluded_op_raises(self):
        # concat takes multiple inputs, can't be in a single-input pipeline
        with pytest.raises(ValueError, match="not supported"):
            Pipeline("in.mp4", "out.mp4", [PipelineStep("concat", {})])

    def test_extract_audio_excluded(self):
        with pytest.raises(ValueError, match="not supported"):
            Pipeline("in.mp4", "out.mp4", [PipelineStep("extract-audio", {})])

    def test_duplicate_op_raises(self):
        with pytest.raises(ValueError, match="more than once"):
            Pipeline("in.mp4", "out.mp4", [
                PipelineStep("resize", {"percentage": 0.5}),
                PipelineStep("resize", {"percentage": 0.25}),
            ])

    def test_copy_flag_rejected(self):
        with pytest.raises(ValueError, match="copy"):
            Pipeline("in.mp4", "out.mp4", [
                PipelineStep("convert", {"copy": True}),
            ])

    def test_sanitize_without_manual_crop_rejected(self):
        with pytest.raises(ValueError, match="manual"):
            Pipeline("in.mp4", "out.mp4", [
                PipelineStep("sanitize", {}),
            ])


class TestResizeMaxEdgeStep:
    """The Finder video action runs through `pipeline`, not `resize`, so the
    capped-resize mode has to exist on the pipeline step too. A flag that works
    on the standalone verb and silently does nothing in a pipeline is the worse
    failure of the two -- it produces output, just not resized."""

    def test_max_edge_builds_the_clamped_scale_filter(self):
        cmd = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"max_edge": 1280}),
        ]).build_command()
        vf = cmd[cmd.index("-vf") + 1]
        assert "min(iw,1280)" in vf and "min(ih,1280)" in vf
        assert "force_original_aspect_ratio=decrease" in vf
        assert "force_divisible_by=2" in vf

    def test_max_edge_composes_with_a_terminal_convert(self):
        """The preset shape the Quick Action actually uses: cap the size and set
        the quality in one ffmpeg invocation."""
        cmd = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"max_edge": 1280}),
            PipelineStep("convert", {"crf": 28}),
        ]).build_command()
        assert "min(iw,1280)" in cmd[cmd.index("-vf") + 1]
        assert cmd[cmd.index("-crf") + 1] == "28"


class TestBuildCommand:
    def test_resize_only(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"percentage": 0.5}),
        ])
        cmd = p.build_command()
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert "in.mp4" in cmd
        assert "-vf" in cmd
        vf_idx = cmd.index("-vf")
        assert "scale=" in cmd[vf_idx + 1]
        assert cmd[-1] == "out.mp4"

    def test_cut_input_args_before_input(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("cut", {"start": "00:01:00", "end": "00:02:00", "fast_seek": True}),
        ])
        cmd = p.build_command()
        ss_idx = cmd.index("-ss")
        i_idx = cmd.index("-i")
        # Fast seek puts -ss before -i
        assert ss_idx < i_idx

    def test_cut_accurate_seek_after_input(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("cut", {"start": "60", "end": "90", "fast_seek": False}),
        ])
        cmd = p.build_command()
        i_idx = cmd.index("-i")
        # Without fast_seek, -ss is after -i
        ss_idx = cmd.index("-ss", i_idx)
        assert ss_idx > i_idx

    def test_filter_chain_combines_multiple_filters(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"percentage": 0.5}),
            PipelineStep("crop", {"width": 100, "height": 100, "x": 0, "y": 0}),
        ])
        cmd = p.build_command()
        vf_idx = cmd.index("-vf")
        chain = cmd[vf_idx + 1]
        assert "scale=" in chain
        assert "crop=" in chain
        # Comma-separated filter chain
        assert "," in chain

    def test_filter_order_matches_step_order(self):
        # resize then crop -- scale comes before crop in the chain
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"percentage": 0.5}),
            PipelineStep("crop", {"width": 100, "height": 100, "x": 0, "y": 0}),
        ])
        chain = p.build_command()[p.build_command().index("-vf") + 1]
        assert chain.index("scale=") < chain.index("crop=")

    def test_rotate_filter(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("rotate", {"rotation": "90"}),
        ])
        chain = p.build_command()[p.build_command().index("-vf") + 1]
        assert "transpose=" in chain

    def test_convert_sets_codec_at_end(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"percentage": 0.5}),
            PipelineStep("convert", {"crf": 20}),
        ])
        cmd = p.build_command()
        assert "-c:v" in cmd
        assert "-crf" in cmd
        crf_idx = cmd.index("-crf")
        assert cmd[crf_idx + 1] == "20"

    def test_convert_uses_video_audio_codec_keys(self):
        # Param keys match the unified CLI flag names (--video-codec/--audio-codec).
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("convert", {"video_codec": "libx265", "audio_codec": "libopus",
                                     "video_bitrate": "2M", "audio_bitrate": "128k"}),
        ])
        cmd = p.build_command()
        assert cmd[cmd.index("-c:v") + 1] == "libx265"
        assert cmd[cmd.index("-c:a") + 1] == "libopus"
        assert cmd[cmd.index("-b:v") + 1] == "2M"
        assert cmd[cmd.index("-b:a") + 1] == "128k"

    def test_default_codec_when_no_convert(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"percentage": 0.5}),
        ])
        cmd = p.build_command()
        # Even without explicit convert step, we need a video codec
        # because filters require re-encoding
        assert "-c:v" in cmd

    def test_mp4_gets_faststart(self):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"percentage": 0.5}),
        ])
        cmd = p.build_command()
        assert "-movflags" in cmd
        assert "+faststart" in cmd

    def test_non_mp4_no_faststart(self):
        p = Pipeline("in.mp4", "out.mkv", [
            PipelineStep("resize", {"percentage": 0.5}),
        ])
        cmd = p.build_command()
        assert "+faststart" not in cmd


class TestRun:
    @patch("vidtools.pipeline.run_ffmpeg")
    def test_run_invokes_ffmpeg(self, mock_run):
        p = Pipeline("in.mp4", "out.mp4", [
            PipelineStep("resize", {"percentage": 0.5}),
        ])
        p.run()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "ffmpeg"

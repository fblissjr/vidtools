"""Operation pipelines: chain multiple video filters into a single ffmpeg
invocation. Faster than sequential temp files and avoids quality loss from
multiple re-encode passes.

v1 supports: trim/cut (input/output time args), resize, crop, rotate, sanitize,
subtitles (filter chain), and convert (terminal codec/quality).

Excluded: concat (multiple inputs), slideshow (its own pipeline),
extract-audio/extract-frames (different output type), add-audio (multiple
inputs), preset (already a recipe), and audio/image-only operations.
"""

from dataclasses import dataclass

from .utils import run_ffmpeg
from .video_ops import (
    _add_faststart_if_mp4,
    _build_scale_filter,
    _build_transpose_filter,
    _escape_subtitle_path,
)


@dataclass
class PipelineStep:
    """One step in a video processing pipeline."""
    name: str
    params: dict


_FILTER_BUILDERS = {
    "resize": lambda p: _build_scale_filter(p.get("percentage"), p.get("width"),
                                            p.get("height"), p.get("algorithm", "lanczos")),
    "crop": lambda p: f"crop={p['width']}:{p['height']}:{p.get('x', 0)}:{p.get('y', 0)}",
    "rotate": lambda p: _build_transpose_filter(p["rotation"]),
    "sanitize": lambda p: f"crop={p['manual_crop']},noise=alls={p.get('noise', 6)}:allf=t+u",
    "subtitles": lambda p: f"subtitles='{_escape_subtitle_path(p['subs'])}'",
}

_PIPELINE_OPS = set(_FILTER_BUILDERS) | {"cut", "convert"}

_EXCLUDED_OPS = {
    "concat", "slideshow", "extract-audio", "extract-frames",
    "add-audio", "preset", "info",
}


class Pipeline:
    """A chain of video operations that builds a single ffmpeg command.

    The pipeline structure has three slots:
    - input_args: things before -i (e.g. -ss for fast_seek cut)
    - filters: pieces of the -vf chain (resize, crop, rotate, sanitize, subtitles)
    - output_args: things after -i (e.g. -t/-to for cut, -c:v / -crf for convert)
    """

    def __init__(self, input_file: str, output_file: str, steps: list[PipelineStep]):
        self.input_file = input_file
        self.output_file = output_file
        self.steps = steps
        self._validate()

    def _validate(self) -> None:
        seen: set[str] = set()
        for step in self.steps:
            if step.name in _EXCLUDED_OPS:
                raise ValueError(
                    f"'{step.name}' is not supported in a pipeline (different output type "
                    f"or multiple inputs). Run it as a separate command."
                )
            if step.name not in _PIPELINE_OPS:
                raise ValueError(f"Unknown pipeline op: '{step.name}'")
            if step.name in seen:
                raise ValueError(f"Pipeline op '{step.name}' specified more than once")
            seen.add(step.name)

            if step.name == "convert" and step.params.get("copy"):
                raise ValueError(
                    "'convert --copy' is not allowed in a pipeline -- filters require re-encoding"
                )
            if step.name == "sanitize" and not step.params.get("manual_crop"):
                raise ValueError(
                    "'sanitize' in a pipeline requires --manual-crop "
                    "(probe-based cropdetect not supported in pipeline mode)"
                )

    def _find(self, name: str) -> PipelineStep | None:
        for step in self.steps:
            if step.name == name:
                return step
        return None

    def build_command(self) -> list[str]:
        cmd: list[str] = ["ffmpeg", "-y"]

        # Cut step contributes both input and output time args
        cut = self._find("cut")
        if cut and cut.params.get("fast_seek") and cut.params.get("start"):
            cmd.extend(["-ss", str(cut.params["start"])])

        cmd.extend(["-i", self.input_file])

        if cut and not cut.params.get("fast_seek") and cut.params.get("start"):
            cmd.extend(["-ss", str(cut.params["start"])])
        if cut:
            if cut.params.get("end"):
                cmd.extend(["-to", str(cut.params["end"])])
            elif cut.params.get("duration"):
                cmd.extend(["-t", str(cut.params["duration"])])

        # Filter chain (in declared order)
        filters = [
            _FILTER_BUILDERS[step.name](step.params)
            for step in self.steps
            if step.name in _FILTER_BUILDERS
        ]
        if filters:
            cmd.extend(["-vf", ",".join(filters)])

        # Output args
        convert = self._find("convert")
        if convert:
            cmd.extend(_convert_output_args(convert.params))
        else:
            # Filters require re-encoding -- supply sane defaults
            cmd.extend(["-c:v", "libx264", "-crf", "23", "-c:a", "copy"])

        _add_faststart_if_mp4(cmd, self.output_file)
        cmd.append(self.output_file)
        return cmd

    def run(self) -> None:
        run_ffmpeg(self.build_command())


def _convert_output_args(params: dict) -> list[str]:
    """Codec / quality args for the terminal convert step."""
    args: list[str] = []
    args.extend(["-c:v", params.get("vcodec", "libx264")])
    args.extend(["-c:a", params.get("acodec", "aac")])
    if "vbitrate" in params and params["vbitrate"]:
        args.extend(["-b:v", str(params["vbitrate"])])
    if "abitrate" in params and params["abitrate"]:
        args.extend(["-b:a", str(params["abitrate"])])
    if "crf" in params and params["crf"] is not None:
        args.extend(["-crf", str(params["crf"])])
    else:
        args.extend(["-crf", "23"])
    if "preset" in params and params["preset"]:
        args.extend(["-preset", str(params["preset"])])
    return args

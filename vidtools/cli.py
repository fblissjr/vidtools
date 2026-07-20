import argparse
import sys
import orjson
from pathlib import Path
from .utils import (
    is_valid_file, check_ffmpeg_installed, VIDEO_EXTS, AUDIO_EXTS, IMAGE_EXTS,
)
from . import operations
from .compress import DEFAULT_TARGET_SSIM, compress_image
from . import image_ops
from . import audio_ops
from . import presets
from . import ytdlp


# --- Input-type dispatch ---
# VIDEO_EXTS / AUDIO_EXTS / IMAGE_EXTS live in utils.py (shared with video_ops).


def _detect_kind(path: str) -> str:
    """Detect media kind from file extension. Raises SystemExit if unknown."""
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    raise SystemExit(
        f"Cannot determine media type from extension '{ext}'. "
        f"Supported video: {', '.join(sorted(VIDEO_EXTS))}; "
        f"audio: {', '.join(sorted(AUDIO_EXTS))}; "
        f"image: {', '.join(sorted(IMAGE_EXTS))}."
    )


#: Verbs whose media kind is decided by the input file, not the verb.
_SMART_COMMANDS = frozenset({"info", "convert", "resize", "crop", "trim", "cut", "rotate"})

# Verbs that have a media-specific alternative when the input kind isn't supported.
_SMART_HINTS = {
    ("resize", "audio"): "Audio has no resolution; use 'resample' to change sample rate.",
    ("crop", "audio"): "Audio can't be cropped; use 'trim' to cut by time.",
    ("trim", "image"): "Images have no duration; use 'crop' for spatial trimming.",
    ("rotate", "audio"): "Audio can't be rotated.",
}


def _make_smart(op_name: str, video=None, image=None, audio=None):
    """Build a dispatcher that routes to the handler matching the input's kind."""
    def dispatch(args):
        kind = _detect_kind(args.input)
        handler = {"video": video, "image": image, "audio": audio}.get(kind)
        if handler is None:
            hint = _SMART_HINTS.get((op_name, kind))
            msg = f"'{op_name}' is not supported for {kind} files"
            raise SystemExit(f"{msg}. {hint}" if hint else msg)
        handler(args)
    return dispatch


# --- Quality mapping (unified 1-100 scale, higher = better) ---

def _quality_to_crf(quality: int) -> int:
    """Map a 1-100 quality knob to an x264/x265 CRF, centered on the encoder default.
    100 -> 16 (near-lossless), 50 -> 23 (x264 default), 1 -> ~30. Power users
    pass --crf directly to bypass this."""
    q = max(1, min(100, quality))
    return round(30 - (q / 100) * 14)


_AUDIO_QUALITY_BITRATES = [(90, "320k"), (75, "256k"), (60, "192k"), (40, "128k"), (20, "96k")]


def _quality_to_audio_bitrate(quality: int) -> str:
    """Map a 1-100 quality knob to a common audio bitrate. Power users pass --bitrate."""
    q = max(1, min(100, quality))
    for threshold, bitrate in _AUDIO_QUALITY_BITRATES:
        if q >= threshold:
            return bitrate
    return "64k"


# --- Parser-building helpers ---

def _add_io(parser, input_help="Input file", output_help="Output file"):
    """Add the standard `input` + `output` positionals."""
    parser.add_argument("input", type=lambda x: is_valid_file(parser, x), help=input_help)
    parser.add_argument("output", help=output_help)


def _parse_size(value: str) -> int:
    """Parse a byte budget like '500k', '2M', or a bare '512000'."""
    text = value.strip().lower().rstrip("b")
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    multiplier = multipliers.get(text[-1:], 1)
    if multiplier != 1:
        text = text[:-1]
    try:
        size = int(float(text) * multiplier)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid size: '{value}'. Use e.g. 500k, 2M, 512000") from None
    if size <= 0:
        raise argparse.ArgumentTypeError(f"Size must be positive, got '{value}'")
    return size


def _add_preserve_metadata(parser):
    """Add `--preserve-metadata` (default off).

    Identifying metadata -- GPS, camera make/model, capture time -- is stripped
    by default. These verbs write a *new* file, so the original keeps
    everything, while a leaked location does not come back.

    Rendering data survives either way: image orientation is baked into pixels
    and the colour profile is carried through; video keeps its display matrix
    (on a stream copy) or has the rotation baked in by the re-encode.

    Granularity differs by medium, because the underlying tools differ. For
    images the kept set is an allowlist, so `--preserve-metadata` restores
    authorship tags (copyright, artist, description) without re-admitting GPS.
    For video, ffmpeg's `-map_metadata` is all-or-nothing at the container
    level, so the flag keeps *everything* -- including location.
    """
    parser.add_argument(
        "--preserve-metadata", action="store_true",
        help="Keep source metadata. Images: authorship tags only, never GPS. "
             "Video: all container tags, including GPS. Off by default.")


def _add_time_range(parser):
    """Add the standard `--start` / `--end` / `--duration` time-range flags."""
    parser.add_argument("--start", dest="start_time", help="Start time (e.g. 00:01:30, 90, 1:30)")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--end", dest="end_time", help="End time (absolute timestamp)")
    grp.add_argument("--duration", help="Duration from the start time")


def _audio_verb(run_audio_op):
    """Adapt an audio operation so the verb also works on video inputs.

    `run_audio_op(args, in_path, out_path)` runs the underlying audio op. The
    audio-output-vs-video-remux routing lives in `apply_audio_op_keep_video`
    (shared with the web `clean` dispatch), so e.g. `normalize clip.mp4 out.mp4`
    keeps the video and fixes the audio, while `... out.mp3` extracts audio."""
    def handler(args):
        operations.apply_audio_op_keep_video(
            args.input, args.output,
            lambda i, o: run_audio_op(args, i, o),
        )
    return handler


# ──────────────────────────────────────────────────────────────────────────────
def setup_argparse() -> argparse.ArgumentParser:
    """Return the top-level argument parser with all sub-commands registered.

    Canonical shape:  vidtools <operation> [params] [--format] [power-user flags] <input> <output>

    Multi-media verbs (info, convert, resize, crop, trim, rotate) auto-detect the
    input type and do the right thing -- you don't pick audio vs video vs image.
    Single-media verbs (normalize, thumbnail, ...) are top-level too (no prefixes)."""
    parser = argparse.ArgumentParser(
        description="vidtools - FFmpeg + PIL + yt-dlp media helper CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        help="Available operations",
    )

    # ════════════════════════════════════════════════ SMART (multi-media) ════

    # ---------------------------------------------------------------- info ----
    info = subparsers.add_parser(
        "info", help="Show media info (video, audio, or image)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    info.add_argument("input", type=lambda x: is_valid_file(info, x))
    def _info_video(args):
        print(orjson.dumps(operations.get_video_info(args.input), option=orjson.OPT_INDENT_2).decode())
    def _info_image(args):
        print(orjson.dumps(image_ops.get_image_info(args.input), option=orjson.OPT_INDENT_2).decode())
    def _info_audio(args):
        print(orjson.dumps(audio_ops.get_audio_info(args.input), option=orjson.OPT_INDENT_2).decode())
    info.set_defaults(func=_make_smart("info", video=_info_video, image=_info_image, audio=_info_audio))

    # ---------------------------------------------------------------- convert -
    convert = subparsers.add_parser(
        "convert",
        help="Convert format (video/audio/image). Audio-only output extracts audio.",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Convert between formats. The output extension decides the target;
an audio-only output from a video (e.g. .mp3) extracts the audio.

Quality: --quality is a unified 1-100 knob (higher = better). Power users can
override per-codec with --crf (video) or --bitrate (audio).

Examples:
  vidtools convert input.avi output.mp4
  vidtools convert input.mp4 output.mp3            # extract audio
  vidtools convert photo.png photo.webp -q 90
  vidtools convert clip.mp4 clip.webm --video-codec libvpx-vp9 --audio-codec libopus"""
    )
    convert.add_argument("-f", "--format", help="Output format (default: from output extension)")
    convert.add_argument("-q", "--quality", type=int, help="Quality 1-100 (higher = better)")
    convert.add_argument("--crf", type=int, help="x264/x265 CRF override (0-51, video; wins over --quality)")
    convert.add_argument("--bitrate", help="Audio bitrate override, e.g. 320k (wins over --quality)")
    convert.add_argument("--video-codec", help="Video codec (e.g. libx264, libvpx-vp9)")
    convert.add_argument("--audio-codec", help="Audio codec (e.g. aac, libopus)")
    convert.add_argument("--video-bitrate", help="Video bitrate (e.g. 2M)")
    convert.add_argument(
        "--preset",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
        help="x264/x265 encoding preset (speed vs compression)"
    )
    convert.add_argument("--copy", action="store_true", help="Stream-copy where possible (no re-encode)")
    convert.add_argument("--hwaccel", action="store_true", help="Hardware acceleration (macOS VideoToolbox)")
    _add_time_range(convert)
    _add_preserve_metadata(convert)
    _add_io(convert)
    def _convert_video(args):
        fmt = args.format or Path(args.output).suffix.lstrip(".")
        crf = args.crf
        if crf is None and args.quality is not None:
            crf = _quality_to_crf(args.quality)
        operations.convert_format(
            args.input, args.output, fmt,
            video_codec=args.video_codec, audio_codec=args.audio_codec,
            video_bitrate=args.video_bitrate, audio_bitrate=args.bitrate,
            quality_scale=crf, start_time=args.start_time, end_time=args.end_time,
            duration=args.duration, preset=args.preset, use_copy=args.copy, hwaccel=args.hwaccel,
            preserve_metadata=args.preserve_metadata)
    def _convert_image(args):
        image_ops.convert_image(args.input, args.output,
                                quality=args.quality if args.quality is not None else 85,
                                preserve_metadata=args.preserve_metadata)
    def _convert_audio(args):
        bitrate = args.bitrate
        if bitrate is None and args.quality is not None:
            bitrate = _quality_to_audio_bitrate(args.quality)
        audio_ops.convert_audio(args.input, args.output, audio_codec=args.audio_codec,
                                bitrate=bitrate, preserve_metadata=args.preserve_metadata)
    def _convert_dispatch(args):
        # An audio-only OUTPUT extracts/encodes audio regardless of input kind,
        # so --quality maps to bitrate (not video CRF) on that path. Otherwise
        # route by input type.
        if Path(args.output).suffix.lower() in AUDIO_EXTS:
            _convert_audio(args)
        else:
            _make_smart("convert", video=_convert_video,
                        image=_convert_image, audio=_convert_audio)(args)
    convert.set_defaults(func=_convert_dispatch)

    # ---------------------------------------------------------------- resize --
    resize = subparsers.add_parser(
        "resize", help="Resize a video or image (aspect-preserving)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    grp = resize.add_mutually_exclusive_group(required=True)
    grp.add_argument("-p", "--percentage", type=float, help="Scale by factor (e.g. 0.5)")
    grp.add_argument("-W", "--width", type=int, help="Target width")
    grp.add_argument("-H", "--height", type=int, help="Target height")
    resize.add_argument(
        "--algorithm", default="lanczos",
        choices=["neighbor", "fast_bilinear", "bilinear", "bicubic", "experimental",
                 "area", "bicubiclina", "gauss", "sinc", "lanczos", "spline"],
        help="Scaling algorithm (default: lanczos)",
    )
    _add_preserve_metadata(resize)
    _add_io(resize)
    def _resize_video(args):
        operations.resize_video(args.input, args.output, percentage=args.percentage,
                                width=args.width, height=args.height, algorithm=args.algorithm,
                                preserve_metadata=args.preserve_metadata)
    def _resize_image(args):
        image_ops.resize_image(args.input, args.output, percentage=args.percentage,
                               width=args.width, height=args.height,
                               preserve_metadata=args.preserve_metadata)
    resize.set_defaults(func=_make_smart("resize", video=_resize_video, image=_resize_image))

    # ---------------------------------------------------------------- crop ----
    crop = subparsers.add_parser(
        "crop", help="Crop a video or image to W:H at offset x,y",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    crop.add_argument("-W", "--width", type=int, required=True, help="Crop width")
    crop.add_argument("-H", "--height", type=int, required=True, help="Crop height")
    crop.add_argument("-x", type=int, default=0, help="X offset (default: 0)")
    crop.add_argument("-y", type=int, default=0, help="Y offset (default: 0)")
    _add_preserve_metadata(crop)
    _add_io(crop)
    def _crop_video(args):
        operations.crop_video(args.input, args.output, args.width, args.height, args.x, args.y,
                              preserve_metadata=args.preserve_metadata)
    def _crop_image(args):
        image_ops.crop_image(args.input, args.output, args.width, args.height, args.x, args.y,
                             preserve_metadata=args.preserve_metadata)
    crop.set_defaults(func=_make_smart("crop", video=_crop_video, image=_crop_image))

    # ---------------------------------------------------------------- trim ----
    trim = subparsers.add_parser(
        "trim", aliases=["cut"],
        help="Trim a video or audio clip by time range (stream-copy by default)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Trim a clip by time range. Works on video or audio.

Stream-copies by default (fast, lossless). Video-only power-user options
(--accurate, --fast-seek, codecs, --crf) refine the cut.

Examples:
  vidtools trim --start 00:00:40 --end 00:01:06.500 input.mp4 output.mp4
  vidtools trim --start 40 --duration 26.5 --accurate input.mp4 output.mp4
  vidtools trim --start 10 --end 30 song.mp3 clip.mp3

Time formats: HH:MM:SS.mmm, seconds (66.5), or MM:SS"""
    )
    _add_time_range(trim)
    _add_preserve_metadata(trim)
    trim.add_argument("--accurate", action="store_true",
                      help="Frame-accurate (re-encodes, slower; video only)")
    trim.add_argument("--fast-seek", action="store_true",
                      help="Seek before input (fastest, less precise; video only)")
    trim.add_argument("--fix-sync", action="store_true", help="Fix audio sync (video only)")
    trim.add_argument("--video-codec", help="Video codec for re-encode (with --accurate)")
    trim.add_argument("--audio-codec", help="Audio codec for re-encode (with --accurate)")
    trim.add_argument("--crf", type=int, help="CRF for re-encode (0-51; video only)")
    trim.add_argument("--hwaccel", action="store_true", help="Hardware acceleration (macOS VideoToolbox)")
    _add_io(trim, "Input video or audio")
    def _trim_video(args):
        operations.cut_video(args.input, args.output, start_time=args.start_time,
                             end_time=args.end_time, duration=args.duration,
                             use_copy=not args.accurate, fast_seek=args.fast_seek,
                             fix_sync=args.fix_sync, video_codec=args.video_codec,
                             audio_codec=args.audio_codec, crf=args.crf, hwaccel=args.hwaccel,
                             preserve_metadata=args.preserve_metadata)
    def _trim_audio(args):
        audio_ops.cut_audio(args.input, args.output, start_time=args.start_time,
                            end_time=args.end_time, duration=args.duration,
                            preserve_metadata=args.preserve_metadata)
    trim.set_defaults(func=_make_smart("trim", video=_trim_video, audio=_trim_audio))

    # ---------------------------------------------------------------- rotate --
    rotate = subparsers.add_parser(
        "rotate", help="Rotate a video or image",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    rotate.add_argument("-r", "--rotation", required=True,
                        help="Degrees. Video: 90/180/270/-90. Image: any angle (clockwise).")
    _add_preserve_metadata(rotate)
    _add_io(rotate)
    def _rotate_video(args):
        operations.rotate_video(args.input, args.output, args.rotation,
                                preserve_metadata=args.preserve_metadata)
    def _rotate_image(args):
        try:
            angle = float(args.rotation)
        except ValueError:
            raise ValueError(f"Invalid rotation {args.rotation!r}: expected a number of degrees")
        image_ops.adjust_image(args.input, args.output, rotation=angle,
                               preserve_metadata=args.preserve_metadata)
    rotate.set_defaults(func=_make_smart("rotate", video=_rotate_video, image=_rotate_image))

    # ═══════════════════════════════════════════════════════ VIDEO-ONLY ══════

    # ---------------------------------------------------------------- sanitize
    sanitize = subparsers.add_parser(
        "sanitize",
        help="Auto-crop letterbox, add noise, re-encode A+V, strip metadata",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Defeat common content-fingerprinting on a video:
  - Auto-crop letterbox using cropdetect (full W:H:X:Y, not just bottom)
  - Add light noise to defeat perceptual-hash matching
  - Re-encode video (libx264) and audio (AAC by default) to break hash matches
  - Strip container AND stream metadata (-map_metadata + -map_metadata:s)
  - Suppress ffmpeg encoder string in output (-fflags +bitexact)

Use --keep-audio to preserve original audio bytes; --no-audio to strip audio.""",
    )
    sanitize.add_argument("--limit", type=int, default=24, help="cropdetect limit (default 24)")
    sanitize.add_argument("--noise", type=int, default=6, help="Noise amplitude 0-100 (default 6)")
    sanitize.add_argument("--extra-bottom", type=int, default=0, metavar="PX",
                          help="Crop PX more off the bottom after auto-detect")
    sanitize.add_argument("--manual-crop", metavar="EXPR", default=None,
                          help="Explicit crop expression W:H:X:Y. Skips auto-detect.")
    sanitize.add_argument("--crf", type=int, default=22, help="x264 CRF quality (default 22)")
    audio_grp = sanitize.add_mutually_exclusive_group()
    audio_grp.add_argument("--keep-audio", action="store_true",
                           help="Stream-copy original audio (fingerprints survive)")
    audio_grp.add_argument("--no-audio", action="store_true", help="Strip audio entirely")
    _add_io(sanitize)
    def _sanitize(args):
        mode = "none" if args.no_audio else "copy" if args.keep_audio else "aac"
        operations.sanitize_video(input_file=args.input, output_file=args.output, limit=args.limit,
                                  noise=args.noise, crf=args.crf, extra_bottom=args.extra_bottom,
                                  manual_crop=args.manual_crop, audio_mode=mode)
    sanitize.set_defaults(func=_sanitize)

    # ---------------------------------------------------------------- extract-audio
    extract_audio = subparsers.add_parser(
        "extract-audio", help="Extract the audio track from a video",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    extract_audio.add_argument("-f", "--format", default="copy",
                               help="Audio format (copy, mp3, aac, flac, ...). Default: copy")
    _add_time_range(extract_audio)
    _add_io(extract_audio, "Input video", "Output audio file")
    def _extract_audio(args):
        operations.extract_audio(args.input, args.output, args.format, args.start_time,
                                 args.end_time, args.duration)
    extract_audio.set_defaults(func=_extract_audio)

    # ---------------------------------------------------------------- extract-frames
    extract_frames = subparsers.add_parser(
        "extract-frames", help="Extract frames from a video as images",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    extract_frames.add_argument("-r", "--rate", type=float, default=1,
                                help="Frame rate (fps) to extract. Default: 1")
    extract_frames.add_argument("--format", default="image2", help="Muxer/format. Default: image2")
    _add_time_range(extract_frames)
    _add_io(extract_frames, "Input video", "Output pattern (e.g. frame_%%04d.jpg)")
    def _extract_frames(args):
        operations.extract_frames(args.input, args.output, args.rate, args.format, args.start_time,
                                  args.end_time, args.duration)
    extract_frames.set_defaults(func=_extract_frames)

    # ---------------------------------------------------------------- screenshot
    screenshot_p = subparsers.add_parser(
        "screenshot",
        help="Grab a single high-quality still frame at a timestamp",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Grab one still frame from a video at a given timestamp.

Output format comes from the output extension (.png/.jpg/.webp). PNG is
lossless (best fidelity); jpg/webp re-encode via PIL with --quality.

Examples:
  vidtools screenshot --at 00:02:00 in.mp4 shot.png
  vidtools screenshot --at 90.5 --quality 92 in.mp4 shot.jpg
  vidtools screenshot --at 00:02:00 --accurate in.mp4 shot.png""",
    )
    screenshot_p.add_argument("--at", dest="timestamp", default=None,
                              help="Timestamp to grab (e.g. 00:01:30, 90, 90.5). Default: first frame")
    screenshot_p.add_argument("-q", "--quality", type=int, default=95,
                              help="Quality 1-100 for jpg/webp (higher = better). Ignored for PNG. Default: 95")
    screenshot_p.add_argument("--accurate", action="store_true",
                              help="Decode to the exact frame (slower; default uses fast keyframe seek)")
    _add_io(screenshot_p, "Input video", "Output image (.png/.jpg/.webp)")
    def _screenshot(args):
        operations.screenshot(args.input, args.output, timestamp=args.timestamp,
                              quality=args.quality, accurate=args.accurate)
    screenshot_p.set_defaults(func=_screenshot)

    # ---------------------------------------------------------------- concat ---
    concat = subparsers.add_parser(
        "concat", help="Concatenate videos (same codec required for stream copy)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Concatenate multiple videos into one.

For stream-copy mode, all inputs must share codec parameters."""
    )
    concat.add_argument("inputs", nargs="+", help="Input video files")
    concat.add_argument("-o", "--output", required=True, help="Output file")
    concat.add_argument("--reencode", action="store_true",
                        help="Re-encode (slower, handles different codecs)")
    _add_preserve_metadata(concat)
    def _concat(args):
        operations.concatenate_videos(args.inputs, args.output, use_copy=not args.reencode,
                                      preserve_metadata=args.preserve_metadata)
    concat.set_defaults(func=_concat)

    # ---------------------------------------------------------------- subtitles
    subtitles = subparsers.add_parser(
        "subtitles", help="Burn subtitles into a video",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subtitles.add_argument("-s", "--subs", required=True,
                           type=lambda x: is_valid_file(subtitles, x),
                           help="Subtitle file (.srt, .ass, ...)")
    _add_preserve_metadata(subtitles)
    _add_io(subtitles, "Input video")
    def _subtitles(args):
        operations.add_subtitles(args.input, args.output, args.subs,
                                 preserve_metadata=args.preserve_metadata)
    subtitles.set_defaults(func=_subtitles)

    # ---------------------------------------------------------------- add-audio
    add_audio_parser = subparsers.add_parser(
        "add-audio", help="Add an audio track to a video (replace or mix)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Add an audio file to a video. Replaces the existing track by
default; use --mix to layer on top.

Examples:
  vidtools add-audio video.mp4 music.mp3 output.mp4
  vidtools add-audio --mix video.mp4 narration.wav output.mp4
  vidtools add-audio --mix --volume 0.3 video.mp4 bg.mp3 output.mp4"""
    )
    add_audio_parser.add_argument("--mix", action="store_true",
                                  help="Layer new audio on top of existing (default: replace)")
    add_audio_parser.add_argument("--volume", "-v", type=float, default=1.0,
                                  help="Volume multiplier for the added audio (0.0-10.0, default: 1.0)")
    add_audio_parser.add_argument("input", type=lambda x: is_valid_file(add_audio_parser, x),
                                  help="Input video file")
    add_audio_parser.add_argument("audio", type=lambda x: is_valid_file(add_audio_parser, x),
                                  help="Audio file to add")
    _add_preserve_metadata(add_audio_parser)
    add_audio_parser.add_argument("output", help="Output video file")
    def _add_audio(args):
        operations.add_audio(args.input, args.audio, args.output,
                             mode="mix" if args.mix else "replace", volume=args.volume,
                             preserve_metadata=args.preserve_metadata)
    add_audio_parser.set_defaults(func=_add_audio)

    # ---------------------------------------------------------------- match-duration
    match_duration = subparsers.add_parser(
        "match-duration",
        help="Trim a video so its duration matches its audio track + a small buffer",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Trim a video whose video track outlasts its audio track. Probes
both streams and applies -t (audio_duration + buffer). Stream-copies by default;
--accurate re-encodes for frame-accurate trimming.

Examples:
  vidtools match-duration in.mp4 out.mp4
  vidtools match-duration --buffer 0.5 in.mp4 out.mp4
  vidtools match-duration --accurate --crf 20 in.mp4 out.mp4"""
    )
    match_duration.add_argument("--buffer", type=float, default=0.25,
                                help="Seconds of video to keep after audio ends (default: 0.25)")
    match_duration.add_argument("--accurate", action="store_true",
                                help="Frame-accurate trim (re-encodes, slower)")
    match_duration.add_argument("--video-codec", help="Video codec for re-encode (with --accurate)")
    match_duration.add_argument("--audio-codec", help="Audio codec for re-encode (with --accurate)")
    match_duration.add_argument("--crf", type=int, help="CRF quality (0-51) for re-encode")
    match_duration.add_argument("--hwaccel", action="store_true",
                                help="Hardware acceleration (macOS VideoToolbox)")
    _add_preserve_metadata(match_duration)
    _add_io(match_duration, "Input video")
    def _match_duration(args):
        operations.trim_to_audio(args.input, args.output, buffer=args.buffer,
                                 accurate=args.accurate, video_codec=args.video_codec,
                                 audio_codec=args.audio_codec, crf=args.crf, hwaccel=args.hwaccel,
                                 preserve_metadata=args.preserve_metadata)
    match_duration.set_defaults(func=_match_duration)

    # ---------------------------------------------------------------- slideshow
    slideshow = subparsers.add_parser(
        "slideshow", help="Create a slideshow video from a sequence of images",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Create a slideshow from a batch of images with smart resizing.

Examples:
  vidtools slideshow --size 1920x1080 --resize-mode pad "images/*.png" out.mp4
  vidtools slideshow --size 1280x720 --resize-mode crop "images/*.jpg" out.mp4
"""
    )
    slideshow.add_argument("-d", "--duration", type=float, default=3,
                           help="Seconds per image (default: 3)")
    slideshow.add_argument("--fps", type=int, default=24, help="Frames per second (default: 24)")
    slideshow.add_argument("--size", help="Output size WxH (default: first image size)")
    slideshow.add_argument("--resize-mode", choices=["stretch", "pad", "crop"], default="stretch",
                           help="Aspect-ratio handling (default: stretch)")
    slideshow.add_argument("--pad-color", default="0,0,0",
                           help="RGB padding color for 'pad' mode (e.g. '255,255,255')")
    slideshow.add_argument("--audio", type=lambda x: is_valid_file(slideshow, x),
                           help="Audio file to add as a soundtrack")
    slideshow.add_argument("--sanitize-crop", help="Crop EACH image before resizing. Format W:H:X:Y")
    slideshow.add_argument("--sanitize-noise", type=int, help="Add noise to EACH image (0-100)")
    slideshow.add_argument("pattern", help="Input image glob (e.g. 'images/*.png')")
    slideshow.add_argument("output", help="Output video file")
    def _slideshow(args):
        operations.create_slideshow(args.pattern, args.output, args.duration, args.fps,
                                    size=args.size, audio_file=args.audio,
                                    resize_mode=args.resize_mode, pad_color=args.pad_color,
                                    sanitize_crop=args.sanitize_crop, sanitize_noise=args.sanitize_noise)
    slideshow.set_defaults(func=_slideshow)

    # ---------------------------------------------------------------- srt-from-transcript
    srt_parser = subparsers.add_parser(
        "srt-from-transcript", help="Convert a timed transcript to a .srt subtitle file",
        formatter_class=argparse.RawTextHelpFormatter,
        description="Converts a transcript file with simple second-based timestamps."
    )
    _add_io(srt_parser, "Input transcript text file", "Output .srt subtitle file")
    def _srt(args):
        operations.create_srt_from_transcript(args.input, args.output)
    srt_parser.set_defaults(func=_srt)

    # ═══════════════════════════════════════════════════════ AUDIO-ONLY ══════

    # Audio-processing verbs accept video inputs too (process the audio track).
    # ---------------------------------------------------------------- normalize
    normalize = subparsers.add_parser("normalize", help="Normalize loudness (two-pass LUFS); audio or video",
                                      formatter_class=argparse.RawTextHelpFormatter)
    normalize.add_argument("--target", type=float, default=-16.0, help="Target LUFS (default: -16)")
    _add_preserve_metadata(normalize)
    _add_io(normalize, "Input audio or video")
    def _normalize(args, in_path, out_path):
        audio_ops.normalize_audio(in_path, out_path, target_loudness=args.target,
                                  preserve_metadata=args.preserve_metadata)
    normalize.set_defaults(func=_audio_verb(_normalize))

    # ---------------------------------------------------------------- fade
    fade = subparsers.add_parser("fade", help="Apply audio fade in/out; audio or video",
                                 formatter_class=argparse.RawTextHelpFormatter)
    fade.add_argument("--fade-in", type=float, help="Fade-in duration (seconds)")
    fade.add_argument("--fade-out", type=float, help="Fade-out duration (seconds)")
    fade.add_argument("--total-duration", type=float, help="Total duration (required for fade-out)")
    _add_preserve_metadata(fade)
    _add_io(fade, "Input audio or video")
    def _fade(args, in_path, out_path):
        audio_ops.fade_audio(in_path, out_path, fade_in=args.fade_in,
                             fade_out=args.fade_out, total_duration=args.total_duration,
                             preserve_metadata=args.preserve_metadata)
    fade.set_defaults(func=_audio_verb(_fade))

    # ---------------------------------------------------------------- resample
    resample = subparsers.add_parser("resample", help="Resample audio (change sample rate); audio or video",
                                     formatter_class=argparse.RawTextHelpFormatter)
    resample.add_argument("-r", "--rate", type=int, default=44100, help="Sample rate Hz (default: 44100)")
    _add_preserve_metadata(resample)
    _add_io(resample, "Input audio or video")
    def _resample(args, in_path, out_path):
        audio_ops.resample_audio(in_path, out_path, sample_rate=args.rate,
                                 preserve_metadata=args.preserve_metadata)
    resample.set_defaults(func=_audio_verb(_resample))

    # ---------------------------------------------------------------- clean
    clean = subparsers.add_parser(
        "clean", help="Clean up vocal audio (highpass + denoise + compress + loudnorm)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Single-pass ffmpeg vocal cleanup chain, tuned for spoken/sung lead.

Stages (each independently skippable):
  1. highpass    -- removes rumble, plosives, AC hum (default 80 Hz)
  2. afftdn      -- FFT noise reduction, removes hiss (default nf=-25)
  3. acompressor -- evens out dynamics
  4. loudnorm    -- EBU R128 to -14 LUFS / -1 dBTP / LRA 11 (streaming target)

Examples:
  vidtools clean in.mp3 out.mp3
  vidtools clean --highpass 60 in.mp3 out.mp3
  vidtools clean --no-denoise in.mp3 out.mp3
  vidtools clean --lufs -23 --true-peak -2.0 in.mp3 out.mp3"""
    )
    clean.add_argument("--highpass", type=int, default=80,
                       help="High-pass cutoff Hz (default: 80). 0 disables.")
    clean.add_argument("--no-highpass", action="store_true", help="Skip high-pass filter")
    clean.add_argument("--denoise", type=float, default=-25.0, help="afftdn noise floor dB (default: -25)")
    clean.add_argument("--no-denoise", action="store_true", help="Skip FFT denoise")
    clean.add_argument("--no-compress", action="store_true", help="Skip compressor")
    clean.add_argument("--threshold", type=float, default=-15.0, help="Compressor threshold dB (default: -15)")
    clean.add_argument("--ratio", type=float, default=3.0, help="Compressor ratio (default: 3)")
    clean.add_argument("--attack", type=float, default=5.0, help="Compressor attack ms (default: 5)")
    clean.add_argument("--release", type=float, default=50.0, help="Compressor release ms (default: 50)")
    clean.add_argument("--no-loudnorm", action="store_true", help="Skip loudness normalization")
    clean.add_argument("--lufs", type=float, default=-14.0, help="Target integrated loudness LUFS (default: -14)")
    clean.add_argument("--true-peak", type=float, default=-1.0, help="True peak ceiling dBTP (default: -1.0)")
    clean.add_argument("--lra", type=float, default=11.0, help="Loudness range target (default: 11)")
    clean.add_argument("--audio-codec", dest="audio_codec", help="Audio codec (auto-detected from extension)")
    clean.add_argument("--bitrate", help="Bitrate (e.g. 320k); overrides default VBR for mp3")
    _add_preserve_metadata(clean)
    _add_io(clean, "Input audio or video")
    def _clean(args, in_path, out_path):
        highpass = None if args.no_highpass or args.highpass == 0 else args.highpass
        denoise = None if args.no_denoise else args.denoise
        audio_ops.clean_audio(
            in_path, out_path, highpass=highpass, denoise=denoise,
            compress=not args.no_compress, compress_threshold=args.threshold,
            compress_ratio=args.ratio, compress_attack=args.attack, compress_release=args.release,
            loudnorm=not args.no_loudnorm, target_lufs=args.lufs, true_peak=args.true_peak,
            lra=args.lra, audio_codec=args.audio_codec, bitrate=args.bitrate,
            preserve_metadata=args.preserve_metadata,
        )
    clean.set_defaults(func=_audio_verb(_clean))

    # ---------------------------------------------------------------- separate
    separate = subparsers.add_parser(
        "separate", help="Separate audio into stems (optional extra)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Run a source-separation model and write one file per stem to
OUTPUT_DIR. Requires the 'separation' extra:  uv sync --extra separation

Examples:
  vidtools separate song.mp3 mel_band_roformer_kim_FT_by_aname-tommy.ckpt ./stems/
  vidtools separate song.mp3 htdemucs_ft.yaml ./stems/
  vidtools separate --list"""
    )
    separate.add_argument("input", nargs="?", help="Input audio file")
    separate.add_argument("model", nargs="?", help="Model filename (see --list)")
    separate.add_argument("output_dir", nargs="?", help="Directory to write stems into")
    separate.add_argument("--list", action="store_true", help="List supported models and exit")
    def _separate(args):
        from . import separation
        if args.list:
            try:
                for m in separation.list_models():
                    print(m)
            except ImportError as e:
                raise SystemExit(str(e))
            return
        if not (args.input and args.model and args.output_dir):
            raise SystemExit("separate requires INPUT MODEL OUTPUT_DIR (or --list)")
        try:
            outputs = separation.separate_audio(args.input, args.model, args.output_dir)
        except ImportError as e:
            raise SystemExit(str(e))
        for p in outputs:
            print(p)
    separate.set_defaults(func=_separate)

    # ═══════════════════════════════════════════════════════ IMAGE-ONLY ══════

    # ---------------------------------------------------------------- thumbnail
    thumbnail = subparsers.add_parser("thumbnail", help="Create an aspect-preserving image thumbnail",
                                      formatter_class=argparse.RawTextHelpFormatter)
    thumbnail.add_argument("-s", "--size", type=int, default=256, help="Max dimension (default: 256)")
    _add_preserve_metadata(thumbnail)
    _add_io(thumbnail, "Input image")
    def _thumbnail(args):
        image_ops.create_thumbnail(args.input, args.output, size=args.size,
                                   preserve_metadata=args.preserve_metadata)
    thumbnail.set_defaults(func=_thumbnail)

    # ---------------------------------------------------------------- strip-metadata
    strip_meta = subparsers.add_parser("strip-metadata", help="Remove image metadata (EXIF/IPTC/XMP)",
                                       formatter_class=argparse.RawTextHelpFormatter)
    _add_io(strip_meta, "Input image")
    def _strip_meta(args):
        image_ops.strip_image_metadata(args.input, args.output)
    strip_meta.set_defaults(func=_strip_meta)

    # ---------------------------------------------------------------- optimize
    optimize = subparsers.add_parser("optimize", help="Optimize/compress an image",
                                     formatter_class=argparse.RawTextHelpFormatter)
    optimize.add_argument("-q", "--quality", type=int, default=80, help="Quality 1-100 (default: 80)")
    optimize.add_argument("--max-width", type=int, help="Max width")
    optimize.add_argument("--max-height", type=int, help="Max height")
    _add_preserve_metadata(optimize)
    _add_io(optimize, "Input image")
    def _optimize(args):
        image_ops.optimize_image(args.input, args.output, quality=args.quality,
                                 max_width=args.max_width, max_height=args.max_height,
                                 preserve_metadata=args.preserve_metadata)
    optimize.set_defaults(func=_optimize)

    # ---------------------------------------------------------------- compress
    compress_p = subparsers.add_parser(
        "compress",
        help="Shrink an image, picking the format automatically",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Measure candidate encodings and write the smallest one that
meets a fidelity target.

Unlike `optimize` (which recompresses into the format you name), `compress`
searches formats and qualities, scores each against the original, and reports
what it found. Defaults to PNG/JPEG/WebP -- the formats the Claude and Gemini
vision APIs accept. AVIF usually compresses far better but no major vision API
decodes it, so it is opt-in via --allow-avif.""",
    )
    compress_p.add_argument("input", type=lambda x: is_valid_file(compress_p, x), help="Input image")
    compress_p.add_argument("output", nargs="?",
                          help="Output path. Omit the extension to let the winning format "
                               "supply one; omit entirely for <input>_compressed.<ext>")
    compress_p.add_argument("--target-ssim", type=float, default=DEFAULT_TARGET_SSIM,
                          help=f"Fidelity target 0-1 (default: {DEFAULT_TARGET_SSIM})")
    compress_p.add_argument("--lossless", action="store_true",
                          help="Only exact encodes (SSIM 1.0); smallest wins")
    compress_p.add_argument("--max-size", type=_parse_size,
                          help="Byte budget (e.g. 500k, 2M). Maximises fidelity within it")
    compress_p.add_argument("--allow-avif", action="store_true",
                          help="Allow AVIF. Compresses best, but Claude/Gemini cannot read it")
    compress_p.add_argument("--dry-run", action="store_true",
                          help="Report the ranked candidates without writing a file")

    def _compress(args):
        report = compress_image(
            args.input, args.output,
            target_ssim=args.target_ssim, lossless=args.lossless,
            max_size=args.max_size, allow_avif=args.allow_avif, dry_run=args.dry_run,
        )
        src_mb = report.source_size / 1048576
        print(f"{report.width}x{report.height} {report.content_class}, {src_mb:.2f} MB")
        for cand in report.candidates:
            marker = "->" if cand is report.winner else "  "
            print(f" {marker} {cand.label():18} {cand.size / 1048576:7.2f} MB"
                  f"  {cand.ratio(report.source_size):5.1f}x smaller  SSIM {cand.ssim:.6f}")
        if report.output_path:
            print(f"wrote {report.output_path}")
        elif args.dry_run:
            print("(dry run -- nothing written)")
    compress_p.set_defaults(func=_compress)

    # ---------------------------------------------------------------- adjust
    adjust = subparsers.add_parser("adjust", help="Adjust image brightness/contrast/rotation/flip",
                                   formatter_class=argparse.RawTextHelpFormatter)
    adjust.add_argument("--brightness", type=float, help="Brightness factor (1.0 = no change)")
    adjust.add_argument("--contrast", type=float, help="Contrast factor (1.0 = no change)")
    adjust.add_argument("--rotation", type=int, help="Rotation in degrees (clockwise)")
    adjust.add_argument("--flip", choices=["horizontal", "vertical"], help="Flip direction")
    adjust.add_argument("--fill", help="Corner fill for non-right-angle rotation "
                                        "(colour name or #rrggbb; default transparent)")
    _add_preserve_metadata(adjust)
    _add_io(adjust, "Input image")
    def _adjust(args):
        image_ops.adjust_image(args.input, args.output, brightness=args.brightness,
                               contrast=args.contrast, rotation=args.rotation, flip=args.flip,
                               fill=args.fill, preserve_metadata=args.preserve_metadata)
    adjust.set_defaults(func=_adjust)

    # ══════════════════════════════════════════════════════════════ META ═════

    # ---------------------------------------------------------------- preset
    preset_parser = subparsers.add_parser("preset", help="Manage and apply presets")
    # `vidtools preset` with no sub-action prints help instead of crashing on args.func.
    preset_parser.set_defaults(func=lambda _args: preset_parser.print_help())
    preset_sub = preset_parser.add_subparsers(dest="preset_action")

    apply = preset_sub.add_parser("apply", help="Apply a preset")
    apply.add_argument("input", type=lambda x: is_valid_file(apply, x))
    apply.add_argument("output")
    apply.add_argument("name", help="Preset name")
    apply.set_defaults(func=lambda args: operations.apply_preset(args.input, args.output, args.name))

    list_p = preset_sub.add_parser("list", help="List available presets")
    list_p.set_defaults(func=lambda _args: presets.list_presets())

    delete = preset_sub.add_parser("delete", help="Delete a preset")
    delete.add_argument("name", help="Preset name")
    delete.set_defaults(func=lambda args: presets.delete_preset(args.name))

    edit = preset_sub.add_parser("edit", help="Edit preset file")
    edit.add_argument("name", help="Preset name")
    edit.set_defaults(func=lambda args: presets.edit_preset_command(args.name))

    # ---------------------------------------------------------------- dl-live
    dl_live = subparsers.add_parser(
        "dl-live", help="Record a YouTube/HLS live broadcast",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Record a live broadcast via yt-dlp. Output is always mp4
(forcing .ts triggers a yt-dlp/ffmpeg ARG_MAX overflow on macOS with YouTube's
long signed URLs -- see vidtools/ytdlp.py:build_ytdlp_live_command).

Waits for the stream to start, records from the beginning by default, and
survives network blips via infinite fragment retries. Stop with Ctrl-C; the
in-progress file remains playable.

Examples:
  vidtools dl-live "https://youtube.com/@channel/live" show.mp4
  vidtools dl-live --from-now "https://www.youtube.com/watch?v=<id>" capture.mp4
"""
    )
    dl_live.add_argument("--wait", type=int, default=60,
                         help="Seconds between polls while waiting (default: 60). 0 = fail if not live.")
    dl_live.add_argument("--no-wait", action="store_true", help="Equivalent to --wait 0.")
    dl_live.add_argument("--from-now", action="store_true",
                         help="Join at live edge instead of recording from start.")
    dl_live.add_argument("url", help="Live broadcast URL")
    dl_live.add_argument("output", nargs="?", default="live_recording.mp4",
                         help="Output path (default: live_recording.mp4). Non-mp4 suffixes coerced to .mp4.")
    def _dl_live(args):
        out = Path(args.output)
        if out.suffix.lower() != ".mp4":
            print(f"coercing output {out} -> {out.with_suffix('.mp4')} (mp4 only)", file=sys.stderr)
            out = out.with_suffix(".mp4")
        wait = 0 if args.no_wait else args.wait
        print(ytdlp.download_live(url=args.url, output_path=str(out),
                                  wait_interval=wait, from_start=not args.from_now))
    dl_live.set_defaults(func=_dl_live)

    # ---------------------------------------------------------------- pipeline
    pipeline = subparsers.add_parser(
        "pipeline",
        help="Chain multiple video operations into a single ffmpeg invocation",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Chain video operations into one ffmpeg command -- no intermediate
files, no double re-encoding.

Examples:
  vidtools pipeline in.mp4 out.mp4 trim --start 60 --end 90 resize -p 0.5 convert --crf 20
  vidtools pipeline in.mp4 out.mp4 crop -W 800 -H 600 -x 100 -y 50 rotate -r 90

Supported steps: trim (alias cut), resize, crop, rotate, sanitize, subtitles, convert.
NOT supported: concat, slideshow, extract-*, add-audio, preset.

Step order is preserved in the filter chain. trim input args are pulled before
the input file regardless of position. convert is always terminal."""
    )
    _add_preserve_metadata(pipeline)
    pipeline.add_argument("input", help="Input video file")
    pipeline.add_argument("output", help="Output video file")
    pipeline.add_argument("steps", nargs=argparse.REMAINDER,
                          help="Pipeline steps: <op> [args...] <op> [args...] ...")
    def _pipeline(args):
        from .pipeline import Pipeline
        steps = _parse_pipeline_steps(args.steps)
        if not steps:
            raise SystemExit("pipeline requires at least one step")
        if _detect_kind(args.input) != "video":
            raise SystemExit("pipeline only supports video inputs in v1")
        Pipeline(args.input, args.output, steps,
                 preserve_metadata=args.preserve_metadata).run()
    pipeline.set_defaults(func=_pipeline)

    # ==========================================================================
    return parser


# --- Pipeline argv splitter ---

# "trim" is the canonical verb; "cut" stays as an alias. Both map to the "cut"
# pipeline step internally (pipeline.py keys off "cut").
_PIPELINE_OP_NAMES = {"trim", "cut", "resize", "crop", "rotate", "sanitize", "subtitles", "convert"}


def _canonical_pipeline_op(op: str) -> str:
    return "cut" if op in ("trim", "cut") else op


def _make_step_parser(op: str) -> argparse.ArgumentParser:
    """Build a tiny argparse for one pipeline step."""
    p = argparse.ArgumentParser(prog=f"pipeline {op}", add_help=False)
    if op == "cut":
        p.add_argument("--start", dest="start")
        p.add_argument("--end", dest="end")
        p.add_argument("--duration", dest="duration")
        p.add_argument("--fast-seek", dest="fast_seek", action="store_true")
    elif op == "resize":
        p.add_argument("-p", "--percentage", type=float)
        p.add_argument("-W", "--width", type=int)
        p.add_argument("-H", "--height", type=int)
        p.add_argument("--algorithm", default="lanczos")
    elif op == "crop":
        p.add_argument("-W", "--width", type=int, required=True)
        p.add_argument("-H", "--height", type=int, required=True)
        p.add_argument("-x", type=int, default=0)
        p.add_argument("-y", type=int, default=0)
    elif op == "rotate":
        p.add_argument("-r", "--rotation", required=True,
                       choices=["90", "180", "270", "-90"])
    elif op == "sanitize":
        p.add_argument("--manual-crop", dest="manual_crop", required=True,
                       help="Required in pipeline mode (probe pass not supported)")
        p.add_argument("--noise", type=int, default=6)
    elif op == "subtitles":
        p.add_argument("--subs", dest="subs", required=True)
    elif op == "convert":
        p.add_argument("--video-codec", dest="video_codec")
        p.add_argument("--audio-codec", dest="audio_codec")
        p.add_argument("--video-bitrate", dest="video_bitrate")
        p.add_argument("--audio-bitrate", dest="audio_bitrate")
        p.add_argument("--crf", type=int)
        p.add_argument("--preset")
    else:
        raise ValueError(f"Unknown pipeline op: {op}")
    return p


def _parse_pipeline_steps(argv: list[str]):
    """Walk argv, splitting on operation keywords, parse each step."""
    from .pipeline import PipelineStep
    steps: list = []
    i = 0
    while i < len(argv):
        op = argv[i]
        if op not in _PIPELINE_OP_NAMES:
            raise SystemExit(f"Unknown pipeline op: '{op}'. Valid: {sorted(_PIPELINE_OP_NAMES)}")
        canonical = _canonical_pipeline_op(op)
        j = i + 1
        while j < len(argv) and argv[j] not in _PIPELINE_OP_NAMES:
            j += 1
        step_args = _make_step_parser(canonical).parse_args(argv[i + 1:j])
        # Strip None values so the pipeline validation logic sees clean dicts
        params = {k: v for k, v in vars(step_args).items() if v is not None}
        steps.append(PipelineStep(canonical, params))
        i = j
    return steps

# singleton parser -----------------------------------------------------------
_parser: argparse.ArgumentParser | None = None


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    global _parser
    if _parser is None:
        _parser = setup_argparse()
    return _parser.parse_args(argv)


def _make_cli_sink():
    """Lazy tqdm progress bar -- only created when first progress event arrives.
    Image ops finish without ever reporting progress, so the bar is never shown.
    Surfaces text messages via tqdm postfix so live recording (where percent
    is meaningless) still shows fragment/segment updates."""
    from tqdm import tqdm
    bar = None
    last_msg = ""
    def sink(percent: int, message: str = ""):
        nonlocal bar, last_msg
        if bar is None:
            bar = tqdm(total=100, unit="%", desc="Processing", dynamic_ncols=True)
        if percent > bar.n:
            bar.update(percent - bar.n)
        if message and message != last_msg:
            bar.set_postfix_str(message, refresh=True)
            last_msg = message
        if percent >= 100:
            bar.close()
            bar = None
    return sink


def handle_command(args: argparse.Namespace) -> None:
    from .progress import progress_context
    # A bare command with sub-actions (e.g. `preset`) may leave func unset.
    if args.command and getattr(args, "func", None) is not None:
        with progress_context(_make_cli_sink()):
            args.func(args)
    elif _parser is not None:
        _parser.print_help()


#: Verbs implemented entirely in PIL. These must stay usable on a machine with
#: no ffmpeg installed -- requiring the whole toolchain to resize a PNG is a
#: needless barrier, and it also forced the image tests behind an ffmpeg gate,
#: which is how a family of image-metadata defects went unnoticed.
_PIL_ONLY_COMMANDS = frozenset({
    "compress", "optimize", "thumbnail", "strip-metadata", "adjust",
})


def _needs_ffmpeg(args: argparse.Namespace) -> bool:
    """Whether this invocation actually requires the ffmpeg toolchain.

    Multi-media verbs (`info`, `convert`, `resize`, `crop`, `rotate`) depend on
    the input: an image goes through PIL, anything else through ffmpeg.
    """
    command = getattr(args, "command", None)
    if command in _PIL_ONLY_COMMANDS:
        return False
    source = getattr(args, "input", None)
    if command in _SMART_COMMANDS and isinstance(source, str):
        try:
            return _detect_kind(source) != "image"
        except SystemExit:
            return True  # unknown kind: let the normal error path report it
    return True


def main():
    """Main entry point for the CLI."""
    from .progress import FfmpegError
    try:
        args = parse_arguments()
    except SystemExit:
        raise
    if _needs_ffmpeg(args) and not check_ffmpeg_installed():
        sys.exit("Error: ffmpeg not found in PATH. Please install ffmpeg first.")
    try:
        handle_command(args)
    except (FfmpegError, ValueError) as e:
        # ValueError covers operation-level validation (bad rotation/crop
        # geometry, missing fade args, non-integer rotate angle, etc.) so the
        # user gets a clean message + exit 1 instead of a raw traceback.
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

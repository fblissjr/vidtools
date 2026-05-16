import argparse
import sys
import orjson
from pathlib import Path
from .utils import is_valid_file, check_ffmpeg_installed
from . import operations
from . import image_ops
from . import audio_ops
from . import presets
from . import ytdlp


# --- Input-type dispatch ---

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv",
              ".m4v", ".mpg", ".mpeg", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".m4a", ".wma"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}


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
        f"Cannot auto-detect type from extension '{ext}'. "
        f"Use an explicit command (img-* or audio-*) instead."
    )


def _make_smart(op_name: str, video=None, image=None, audio=None):
    """Build a dispatcher that routes to the handler matching the input's kind."""
    def dispatch(args):
        kind = _detect_kind(args.input)
        handler = {"video": video, "image": image, "audio": audio}.get(kind)
        if handler is None:
            raise SystemExit(f"'{op_name}' is not supported for {kind} files")
        handler(args)
    return dispatch


# ──────────────────────────────────────────────────────────────────────────────
def setup_argparse() -> argparse.ArgumentParser:
    """Return the top‑level argument parser with all sub‑commands registered."""
    parser = argparse.ArgumentParser(
        description="vidtools – FFmpeg helper CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        help="Available operations",
    )

    # ---------------------------------------------------------------- resize --
    resize_parser = subparsers.add_parser(
        "resize",
        help="Resize video resolution",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    resize_parser.add_argument(
        "input", type=lambda x: is_valid_file(resize_parser, x), help="Input video"
    )
    resize_parser.add_argument("output", help="Resized output video")
    grp = resize_parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("-p", "--percentage", type=float, help="Scale by factor (e.g. 0.5)")
    grp.add_argument("-W", "--width", type=int, help="Target width")
    grp.add_argument("-H", "--height", type=int, help="Target height")
    resize_parser.add_argument(
        "--algorithm",
        default="lanczos",
        choices=[
            "neighbor",
            "fast_bilinear",
            "bilinear",
            "bicubic",
            "experimental",
            "area",
            "bicubiclina",
            "gauss",
            "sinc",
            "lanczos",
            "spline",
        ],
        help="FFmpeg scale flags (default: lanczos)",
    )
    def _resize_video(args):
        operations.resize_video(args.input, args.output, percentage=args.percentage,
                                width=args.width, height=args.height, algorithm=args.algorithm)
    def _resize_image(args):
        image_ops.resize_image(args.input, args.output, percentage=args.percentage,
                               width=args.width, height=args.height)
    resize_parser.set_defaults(func=_make_smart("resize", video=_resize_video, image=_resize_image))

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

Use --keep-audio if you need the original audio bytes preserved (faster but
audio fingerprints survive). Use --no-audio to strip audio entirely.""",
    )
    sanitize.add_argument("input",  type=lambda x: is_valid_file(sanitize, x))
    sanitize.add_argument("output")
    sanitize.add_argument(
        "--limit", type=int, default=24, help="cropdetect limit (default 24)"
    )
    sanitize.add_argument(
        "--noise", type=int, default=6, help="Noise amplitude 0-100 (default 6)"
    )
    sanitize.add_argument(
        "--extra-bottom",
        type=int,
        default=0,
        metavar="PX",
        help="Crop PX more pixels off the bottom *after* auto-detect",
    )
    sanitize.add_argument(
        "--manual-crop",
        metavar="EXPR",
        default=None,
        help="Explicit crop expression W:H:X:Y. Skips auto-detect.",
    )
    sanitize.add_argument(
        "--crf", type=int, default=22, help="x264 CRF quality (default 22)"
    )
    audio_grp = sanitize.add_mutually_exclusive_group()
    audio_grp.add_argument(
        "--keep-audio", action="store_true",
        help="Stream-copy original audio (faster but audio fingerprints survive)",
    )
    audio_grp.add_argument(
        "--no-audio", action="store_true",
        help="Strip audio stream entirely",
    )
    def _sanitize(args):
        if args.no_audio:
            mode = "none"
        elif args.keep_audio:
            mode = "copy"
        else:
            mode = "aac"  # default: re-encode
        operations.sanitize_video(input_file=args.input, output_file=args.output, limit=args.limit,
                                  noise=args.noise, crf=args.crf, extra_bottom=args.extra_bottom,
                                  manual_crop=args.manual_crop, audio_mode=mode)
    sanitize.set_defaults(func=_sanitize)

    # ---------------------------------------------------------------- cut -----
    cut = subparsers.add_parser(
        "cut",
        help="Cut/trim video with precise timestamps (uses -c copy by default)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Cut a video segment with various options for speed vs accuracy.

Examples:
  # Fast cut with stream copy (recommended)
  %(prog)s input.mp4 output.mp4 --start 00:00:40 --end 00:01:06.500

  # Frame-accurate cut (slower, re-encodes)
  %(prog)s input.mp4 output.mp4 --start 40 --duration 26.5 --accurate

  # Ultra-fast seeking (seeks before input)
  %(prog)s input.mp4 output.mp4 --start 40 --end 66.5 --fast-seek

Time formats: HH:MM:SS.mmm, seconds (66.5), or MM:SS"""
    )
    cut.add_argument("input", type=lambda x: is_valid_file(cut, x), help="Input video")
    cut.add_argument("output", help="Output video")

    # Time specification group
    cut.add_argument(
        "-ss", "--start", "--from",
        dest="start_time",
        help="Start time (e.g., 00:00:40, 40, 1:30)"
    )

    # End time specification (mutually exclusive)
    end_group = cut.add_mutually_exclusive_group()
    end_group.add_argument(
        "-to", "--end", "--to",
        dest="end_time",
        help="End time (absolute timestamp)"
    )
    end_group.add_argument(
        "-t", "--duration", "--length",
        dest="duration",
        help="Duration from start time"
    )

    # Processing options
    cut.add_argument(
        "--copy", "-c",
        action="store_true",
        default=True,
        help="Use stream copy (fast, no quality loss, default: True)"
    )
    cut.add_argument(
        "--accurate", "--precise",
        action="store_true",
        help="Frame-accurate cut (re-encodes, slower but precise)"
    )
    cut.add_argument(
        "--fast-seek",
        action="store_true",
        help="Seek before input (faster but may be less accurate)"
    )
    cut.add_argument(
        "--fix-sync",
        action="store_true",
        help="Try to fix audio sync issues (uses aresample filter)"
    )
    cut.add_argument(
        "--video-codec", "-vcodec",
        help="Video codec for re-encoding (e.g., libx264)"
    )
    cut.add_argument(
        "--audio-codec", "-acodec",
        help="Audio codec for re-encoding (e.g., aac)"
    )
    cut.add_argument(
        "--crf",
        type=int,
        help="CRF quality for re-encoding (0-51, lower is better)"
    )
    cut.add_argument(
        "--hwaccel", action="store_true",
        help="Use hardware acceleration (macOS VideoToolbox)"
    )
    def _cut_video(args):
        operations.cut_video(args.input, args.output, start_time=args.start_time, end_time=args.end_time,
                             duration=args.duration, use_copy=not args.accurate, fast_seek=args.fast_seek,
                             fix_sync=args.fix_sync, video_codec=args.video_codec,
                             audio_codec=args.audio_codec, crf=args.crf, hwaccel=args.hwaccel)
    def _cut_audio(args):
        audio_ops.cut_audio(args.input, args.output, start_time=args.start_time,
                            end_time=args.end_time, duration=args.duration)
    cut.set_defaults(func=_make_smart("cut", video=_cut_video, audio=_cut_audio))

    # ---------------------------------------------------------------- convert -
    convert = subparsers.add_parser(
        "convert",
        help="Convert video format with smart encoding",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Convert between video formats with optimized settings.

Examples:
  # Convert to MP4 with default settings
  %(prog)s input.avi output.mp4

  # Convert to GIF with optimization
  %(prog)s input.mp4 output.gif

  # Extract audio only
  %(prog)s input.mp4 output.mp3

  # Convert with specific codec
  %(prog)s input.mp4 output.webm --vcodec libvpx-vp9 --acodec libopus"""
    )
    convert.add_argument("input", type=lambda x: is_valid_file(convert, x), help="Input file")
    convert.add_argument("output", help="Output file")
    convert.add_argument(
        "-f", "--format",
        help="Output format (auto-detected from extension if not specified)"
    )
    convert.add_argument("--vcodec", help="Video codec")
    convert.add_argument("--acodec", help="Audio codec")
    convert.add_argument("--vbitrate", help="Video bitrate (e.g., 2M)")
    convert.add_argument("--abitrate", help="Audio bitrate (e.g., 128k)")
    convert.add_argument(
        "--crf", "--quality",
        help="Quality (CRF for x264/x265, 0-51)"
    )
    convert.add_argument(
        "--preset",
        choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
        help="Encoding preset (speed vs compression)"
    )
    convert.add_argument(
        "--copy",
        action="store_true",
        help="Copy streams without re-encoding where possible"
    )
    convert.add_argument(
        "--hwaccel", action="store_true",
        help="Use hardware acceleration (macOS VideoToolbox)"
    )
    # Time range options
    convert.add_argument("--start", dest="start_time", help="Start time")
    convert.add_argument("--end", dest="end_time", help="End time")
    convert.add_argument("--duration", help="Duration")
    def _convert_video(args):
        fmt = args.format or args.output.rsplit('.', 1)[-1]
        operations.convert_format(args.input, args.output, fmt, args.vcodec, args.acodec,
                                  args.vbitrate, args.abitrate, args.crf, args.start_time,
                                  args.end_time, args.duration, args.preset, args.copy,
                                  hwaccel=args.hwaccel)
    def _convert_image(args):
        # Image conversion: use --crf as quality if provided (1-100), else default
        quality = int(args.crf) if args.crf else 85
        image_ops.convert_image(args.input, args.output, quality=quality)
    def _convert_audio(args):
        # Audio conversion: --abitrate maps to bitrate, --acodec to codec
        audio_ops.convert_audio(args.input, args.output,
                                audio_codec=args.acodec, bitrate=args.abitrate)
    convert.set_defaults(func=_make_smart("convert",
                                          video=_convert_video,
                                          image=_convert_image,
                                          audio=_convert_audio))

    # ---------------------------------------------------------------- extract-audio
    extract_audio = subparsers.add_parser(
        "extract-audio",
        help="Extract audio from video",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    extract_audio.add_argument("input", type=lambda x: is_valid_file(extract_audio, x))
    extract_audio.add_argument("output", help="Output audio file")
    extract_audio.add_argument(
        "--format", "-f",
        default="copy",
        help="Audio format (copy, mp3, aac, flac, etc.) Default: copy"
    )
    extract_audio.add_argument("--start", dest="start_time", help="Start time")
    extract_audio.add_argument("--end", dest="end_time", help="End time")
    extract_audio.add_argument("--duration", help="Duration")
    def _extract_audio(args):
        operations.extract_audio(args.input, args.output, args.format, args.start_time,
                                 args.end_time, args.duration)
    extract_audio.set_defaults(func=_extract_audio)

    # ---------------------------------------------------------------- extract-frames
    extract_frames = subparsers.add_parser(
        "extract-frames",
        help="Extract frames as images",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    extract_frames.add_argument("input", type=lambda x: is_valid_file(extract_frames, x))
    extract_frames.add_argument(
        "output",
        help="Output pattern (e.g., frame_%%04d.jpg)"
    )
    extract_frames.add_argument(
        "-r", "--rate",
        type=float,
        default=1,
        help="Frame rate (fps) to extract. Default: 1"
    )
    extract_frames.add_argument(
        "--format",
        default="image2",
        help="Output format. Default: image2"
    )
    extract_frames.add_argument("--start", dest="start_time", help="Start time")
    extract_frames.add_argument("--end", dest="end_time", help="End time")
    extract_frames.add_argument("--duration", help="Duration")
    def _extract_frames(args):
        operations.extract_frames(args.input, args.output, args.rate, args.format, args.start_time,
                                  args.end_time, args.duration)
    extract_frames.set_defaults(func=_extract_frames)

    # ---------------------------------------------------------------- concat ---
    concat = subparsers.add_parser(
        "concat",
        help="Concatenate videos (same codec required for -c copy)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Concatenate multiple videos into one.

Note: For stream copy mode, all inputs must have the same codec parameters."""
    )
    concat.add_argument(
        "inputs",
        nargs="+",
        help="Input video files"
    )
    concat.add_argument("-o", "--output", required=True, help="Output file")
    concat.add_argument(
        "--copy",
        action="store_true",
        default=True,
        help="Use stream copy (fast, requires same codecs)"
    )
    concat.add_argument(
        "--reencode",
        action="store_true",
        help="Re-encode videos (slower, handles different codecs)"
    )
    def _concat(args):
        use_copy = args.copy and not args.reencode
        operations.concatenate_videos(args.inputs, args.output, use_copy=use_copy)
    concat.set_defaults(func=_concat)

    # ---------------------------------------------------------------- crop ----
    crop = subparsers.add_parser(
        "crop",
        help="Crop video to specified dimensions",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    crop.add_argument("input", type=lambda x: is_valid_file(crop, x))
    crop.add_argument("output")
    crop.add_argument("-W", "--width", type=int, required=True, help="Crop width")
    crop.add_argument("-H", "--height", type=int, required=True, help="Crop height")
    crop.add_argument("-x", type=int, default=0, help="X offset (default: 0)")
    crop.add_argument("-y", type=int, default=0, help="Y offset (default: 0)")
    def _crop_video(args):
        operations.crop_video(args.input, args.output, args.width, args.height, args.x, args.y)
    def _crop_image(args):
        image_ops.crop_image(args.input, args.output, args.width, args.height, args.x, args.y)
    crop.set_defaults(func=_make_smart("crop", video=_crop_video, image=_crop_image))

    # ---------------------------------------------------------------- rotate --
    rotate = subparsers.add_parser(
        "rotate",
        help="Rotate video",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    rotate.add_argument("input", type=lambda x: is_valid_file(rotate, x))
    rotate.add_argument("output")
    rotate.add_argument(
        "-r", "--rotation",
        required=True,
        choices=["90", "180", "270", "-90"],
        help="Rotation angle in degrees"
    )
    def _rotate(args):
        operations.rotate_video(args.input, args.output, args.rotation)
    rotate.set_defaults(func=_rotate)

    # ---------------------------------------------------------------- subtitles
    subtitles = subparsers.add_parser(
        "subtitles",
        help="Burn subtitles into video",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    subtitles.add_argument("input", type=lambda x: is_valid_file(subtitles, x))
    subtitles.add_argument("output")
    subtitles.add_argument(
        "-s", "--subs",
        required=True,
        type=lambda x: is_valid_file(subtitles, x),
        help="Subtitle file (.srt, .ass, etc.)"
    )
    def _subtitles(args):
        operations.add_subtitles(args.input, args.output, args.subs)
    subtitles.set_defaults(func=_subtitles)

    # ---------------------------------------------------------------- info ----
    info = subparsers.add_parser(
        "info",
        help="Show video information",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    info.add_argument("input", type=lambda x: is_valid_file(info, x))
    info.add_argument(
        "-j", "--json",
        action="store_true",
        help="Output in JSON format"
    )
    def _info_video(args):
        info_data = operations.get_video_info(args.input)
        print(orjson.dumps(info_data, option=orjson.OPT_INDENT_2).decode())
    def _info_image(args):
        info_data = image_ops.get_image_info(args.input)
        print(orjson.dumps(info_data, option=orjson.OPT_INDENT_2).decode())
    def _info_audio(args):
        info_data = audio_ops.get_audio_info(args.input)
        print(orjson.dumps(info_data, option=orjson.OPT_INDENT_2).decode())
    info.set_defaults(func=_make_smart("info",
                                       video=_info_video,
                                       image=_info_image,
                                       audio=_info_audio))

    # ---------------------------------------------------------------- presets -
    preset_parser = subparsers.add_parser(
        "preset",
        help="Manage and apply presets",
    )
    preset_sub = preset_parser.add_subparsers(dest="preset_action")

    # Apply preset
    apply = preset_sub.add_parser("apply", help="Apply a preset")
    apply.add_argument("input", type=lambda x: is_valid_file(apply, x))
    apply.add_argument("output")
    apply.add_argument("name", help="Preset name")
    def _apply_preset(args):
        operations.apply_preset(args.input, args.output, args.name)
    apply.set_defaults(func=_apply_preset)

    # List presets
    list_p = preset_sub.add_parser("list", help="List available presets")
    def _list_presets(_args):
        presets.list_presets()
    list_p.set_defaults(func=_list_presets)

    # Delete preset
    delete = preset_sub.add_parser("delete", help="Delete a preset")
    delete.add_argument("name", help="Preset name")
    def _delete_preset(args):
        presets.delete_preset(args.name)
    delete.set_defaults(func=_delete_preset)

    # Edit preset
    edit = preset_sub.add_parser("edit", help="Edit preset file")
    edit.add_argument("name", help="Preset name")
    def _edit_preset(args):
        presets.edit_preset_command(args.name)
    edit.set_defaults(func=_edit_preset)

    # ---------------------------------------------------------------- slideshow
    slideshow = subparsers.add_parser(
        "slideshow",
        help="Create a slideshow video from a sequence of images.",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Create a slideshow from a batch of images with smart resizing.

Examples:
  # Create a 1080p slideshow, padding smaller images with black bars
  %(prog)s "images/*.png" out.mp4 --size 1920x1080 --resize-mode pad

  # Create a 720p slideshow, cropping images to fit the frame
  %(prog)s "images/*.jpg" out.mp4 --size 1280x720 --resize-mode crop

  # Sanitize by adding noise, then create a padded slideshow with a blue background
  %(prog)s "raw/*.png" final.mp4 --size 1024x1024 --resize-mode pad --pad-color "0,0,255" --sanitize-noise 5
"""
    )
    slideshow.add_argument("pattern", help="Input image pattern (e.g., 'images/*.png')")
    slideshow.add_argument("output", help="Output video file")
    slideshow.add_argument(
        "-d", "--duration", type=float, default=3,
        help="Duration for each image in seconds (default: 3)"
    )
    slideshow.add_argument(
        "--fps", type=int, default=24, help="Frames per second for the video (default: 24)"
    )
    slideshow.add_argument(
        "--size", help="Output size 'WxH' (e.g., '1920x1080'). Defaults to first image size."
    )
    slideshow.add_argument(
        "--resize-mode", choices=["stretch", "pad", "crop"], default="stretch",
        help="How to handle images with different aspect ratios (default: stretch)."
    )
    slideshow.add_argument(
        "--pad-color", default="0,0,0",
        help="RGB color for padding when using 'pad' mode (e.g., '255,255,255' for white)."
    )
    slideshow.add_argument("--audio", type=lambda x: is_valid_file(slideshow, x),
                           help="Path to an audio file to add as a soundtrack.")
    slideshow.add_argument("--sanitize-crop",
                           help="Sanitize by cropping EACH image before resizing. Format: 'W:H:X:Y'")
    slideshow.add_argument("--sanitize-noise", type=int,
                           help="Sanitize by adding noise to EACH image before resizing. Value 0-100.")
    def _slideshow(args):
        operations.create_slideshow(args.pattern, args.output, args.duration, args.fps,
                                    size=args.size, audio_file=args.audio,
                                    resize_mode=args.resize_mode, pad_color=args.pad_color,
                                    sanitize_crop=args.sanitize_crop,
                                    sanitize_noise=args.sanitize_noise)
    slideshow.set_defaults(func=_slideshow)
    # ---------------------------------------------------------------- add-audio
    add_audio_parser = subparsers.add_parser(
        "add-audio",
        help="Add an audio track to a video (replace or mix)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Add an audio file to a video file.

By default, replaces the existing audio track. Use --mix to layer
the new audio on top of the existing track.

Examples:
  # Replace video audio with a music file
  %(prog)s video.mp4 music.mp3 output.mp4

  # Mix narration on top of existing audio
  %(prog)s video.mp4 narration.wav output.mp4 --mix

  # Replace audio with volume reduced to 50%%
  %(prog)s video.mp4 music.mp3 output.mp4 --volume 0.5

  # Mix with the new track at 30%% volume
  %(prog)s video.mp4 bg_music.mp3 output.mp4 --mix --volume 0.3"""
    )
    add_audio_parser.add_argument(
        "input", type=lambda x: is_valid_file(add_audio_parser, x),
        help="Input video file"
    )
    add_audio_parser.add_argument(
        "audio", type=lambda x: is_valid_file(add_audio_parser, x),
        help="Audio file to add"
    )
    add_audio_parser.add_argument("output", help="Output video file")
    add_audio_parser.add_argument(
        "--mix", action="store_true",
        help="Layer new audio on top of existing (default: replace)"
    )
    add_audio_parser.add_argument(
        "--volume", "-v", type=float, default=1.0,
        help="Volume multiplier for the added audio (0.0-10.0, default: 1.0)"
    )
    def _add_audio(args):
        mode = "mix" if args.mix else "replace"
        operations.add_audio(args.input, args.audio, args.output, mode=mode, volume=args.volume)
    add_audio_parser.set_defaults(func=_add_audio)

    # ---------------------------------------------------------------- trim-to-audio
    trim_audio = subparsers.add_parser(
        "trim-to-audio",
        help="Trim video so its duration matches the audio track + a small buffer",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Trim a video whose video track outlasts its audio track.

Probes both streams via ffprobe, applies -t (audio_duration + buffer) so the
output ends shortly after the audio does. Stream-copies by default; pass
--accurate to re-encode for frame-accurate trimming.

Examples:
  # Default 0.25s buffer
  %(prog)s in.mp4 out.mp4

  # Half-second buffer
  %(prog)s in.mp4 out.mp4 --buffer 0.5

  # Frame-accurate (re-encodes)
  %(prog)s in.mp4 out.mp4 --accurate --crf 20"""
    )
    trim_audio.add_argument("input", type=lambda x: is_valid_file(trim_audio, x), help="Input video")
    trim_audio.add_argument("output", help="Output video")
    trim_audio.add_argument("--buffer", type=float, default=0.25,
                            help="Seconds of video to keep after audio ends (default: 0.25)")
    trim_audio.add_argument("--accurate", action="store_true",
                            help="Frame-accurate trim (re-encodes, slower)")
    trim_audio.add_argument("--video-codec", "-vcodec",
                            help="Video codec for re-encoding (with --accurate)")
    trim_audio.add_argument("--audio-codec", "-acodec",
                            help="Audio codec for re-encoding (with --accurate)")
    trim_audio.add_argument("--crf", type=int,
                            help="CRF quality (0-51) for re-encoding")
    trim_audio.add_argument("--hwaccel", action="store_true",
                            help="Use hardware acceleration (macOS VideoToolbox)")
    def _trim_to_audio(args):
        operations.trim_to_audio(args.input, args.output, buffer=args.buffer,
                                 accurate=args.accurate, video_codec=args.video_codec,
                                 audio_codec=args.audio_codec, crf=args.crf,
                                 hwaccel=args.hwaccel)
    trim_audio.set_defaults(func=_trim_to_audio)

    srt_parser = subparsers.add_parser(
        "srt-from-transcript",
        help="Convert a timed transcript to a .srt subtitle file",
        formatter_class=argparse.RawTextHelpFormatter,
        description="Converts a transcript file with simple second-based timestamps"
    )
    srt_parser.add_argument("input", type=lambda x: is_valid_file(srt_parser, x),
                            help="Input transcript text file")
    srt_parser.add_argument("output", help="Output .srt subtitle file")
    def _srt(args):
        operations.create_srt_from_transcript(args.input, args.output)
    srt_parser.set_defaults(func=_srt)
    # ================================================================ IMAGE ===

    img_resize = subparsers.add_parser("img-resize", help="Resize an image",
                                       formatter_class=argparse.RawTextHelpFormatter)
    img_resize.add_argument("input", type=lambda x: is_valid_file(img_resize, x))
    img_resize.add_argument("output")
    grp_img = img_resize.add_mutually_exclusive_group(required=True)
    grp_img.add_argument("-p", "--percentage", type=float, help="Scale factor (e.g. 0.5)")
    grp_img.add_argument("-W", "--width", type=int, help="Target width")
    grp_img.add_argument("-H", "--height", type=int, help="Target height")
    def _img_resize(args):
        image_ops.resize_image(args.input, args.output, percentage=args.percentage,
                               width=args.width, height=args.height)
    img_resize.set_defaults(func=_img_resize)

    img_convert = subparsers.add_parser("img-convert", help="Convert image format",
                                        formatter_class=argparse.RawTextHelpFormatter)
    img_convert.add_argument("input", type=lambda x: is_valid_file(img_convert, x))
    img_convert.add_argument("output")
    img_convert.add_argument("-q", "--quality", type=int, default=85, help="Quality 1-100 (default: 85)")
    def _img_convert(args):
        image_ops.convert_image(args.input, args.output, quality=args.quality)
    img_convert.set_defaults(func=_img_convert)

    img_crop = subparsers.add_parser("img-crop", help="Crop an image",
                                     formatter_class=argparse.RawTextHelpFormatter)
    img_crop.add_argument("input", type=lambda x: is_valid_file(img_crop, x))
    img_crop.add_argument("output")
    img_crop.add_argument("-W", "--width", type=int, required=True)
    img_crop.add_argument("-H", "--height", type=int, required=True)
    img_crop.add_argument("-x", type=int, default=0)
    img_crop.add_argument("-y", type=int, default=0)
    def _img_crop(args):
        image_ops.crop_image(args.input, args.output, args.width, args.height, args.x, args.y)
    img_crop.set_defaults(func=_img_crop)

    img_thumb = subparsers.add_parser("img-thumb", help="Create a thumbnail",
                                      formatter_class=argparse.RawTextHelpFormatter)
    img_thumb.add_argument("input", type=lambda x: is_valid_file(img_thumb, x))
    img_thumb.add_argument("output")
    img_thumb.add_argument("-s", "--size", type=int, default=256, help="Max dimension (default: 256)")
    def _img_thumb(args):
        image_ops.create_thumbnail(args.input, args.output, size=args.size)
    img_thumb.set_defaults(func=_img_thumb)

    img_strip = subparsers.add_parser("img-strip-exif", help="Remove image metadata",
                                      formatter_class=argparse.RawTextHelpFormatter)
    img_strip.add_argument("input", type=lambda x: is_valid_file(img_strip, x))
    img_strip.add_argument("output")
    def _img_strip(args):
        image_ops.strip_image_metadata(args.input, args.output)
    img_strip.set_defaults(func=_img_strip)

    img_opt = subparsers.add_parser("img-optimize", help="Optimize/compress an image",
                                    formatter_class=argparse.RawTextHelpFormatter)
    img_opt.add_argument("input", type=lambda x: is_valid_file(img_opt, x))
    img_opt.add_argument("output")
    img_opt.add_argument("-q", "--quality", type=int, default=80, help="Quality 1-100 (default: 80)")
    img_opt.add_argument("--max-width", type=int, help="Max width")
    img_opt.add_argument("--max-height", type=int, help="Max height")
    def _img_opt(args):
        image_ops.optimize_image(args.input, args.output, quality=args.quality,
                                 max_width=args.max_width, max_height=args.max_height)
    img_opt.set_defaults(func=_img_opt)

    img_adjust = subparsers.add_parser("img-adjust", help="Adjust brightness/contrast/rotation/flip",
                                       formatter_class=argparse.RawTextHelpFormatter)
    img_adjust.add_argument("input", type=lambda x: is_valid_file(img_adjust, x))
    img_adjust.add_argument("output")
    img_adjust.add_argument("--brightness", type=float, help="Brightness factor (1.0 = no change)")
    img_adjust.add_argument("--contrast", type=float, help="Contrast factor (1.0 = no change)")
    img_adjust.add_argument("--rotation", type=int, help="Rotation in degrees (CW)")
    img_adjust.add_argument("--flip", choices=["horizontal", "vertical"], help="Flip direction")
    def _img_adjust(args):
        image_ops.adjust_image(args.input, args.output, brightness=args.brightness,
                               contrast=args.contrast, rotation=args.rotation, flip=args.flip)
    img_adjust.set_defaults(func=_img_adjust)

    img_info = subparsers.add_parser("img-info", help="Show image information",
                                     formatter_class=argparse.RawTextHelpFormatter)
    img_info.add_argument("input", type=lambda x: is_valid_file(img_info, x))
    def _img_info(args):
        info_data = image_ops.get_image_info(args.input)
        print(orjson.dumps(info_data, option=orjson.OPT_INDENT_2).decode())
    img_info.set_defaults(func=_img_info)

    # ================================================================ AUDIO ==

    aud_convert = subparsers.add_parser("audio-convert", help="Convert audio format",
                                        formatter_class=argparse.RawTextHelpFormatter)
    aud_convert.add_argument("input", type=lambda x: is_valid_file(aud_convert, x))
    aud_convert.add_argument("output")
    aud_convert.add_argument("--codec", dest="audio_codec", help="Audio codec (auto-detected from extension)")
    aud_convert.add_argument("--bitrate", help="Audio bitrate (e.g. 320k)")
    aud_convert.add_argument("--sample-rate", type=int, help="Sample rate (e.g. 44100)")
    def _aud_convert(args):
        audio_ops.convert_audio(args.input, args.output, audio_codec=args.audio_codec,
                                bitrate=args.bitrate, sample_rate=args.sample_rate)
    aud_convert.set_defaults(func=_aud_convert)

    aud_cut = subparsers.add_parser("audio-cut", help="Trim audio by time range",
                                    formatter_class=argparse.RawTextHelpFormatter)
    aud_cut.add_argument("input", type=lambda x: is_valid_file(aud_cut, x))
    aud_cut.add_argument("output")
    aud_cut.add_argument("--start", dest="start_time", help="Start time")
    aud_cut.add_argument("--end", dest="end_time", help="End time")
    aud_cut.add_argument("--duration", help="Duration")
    def _aud_cut(args):
        audio_ops.cut_audio(args.input, args.output, start_time=args.start_time,
                            end_time=args.end_time, duration=args.duration)
    aud_cut.set_defaults(func=_aud_cut)

    aud_normalize = subparsers.add_parser("audio-normalize", help="Normalize audio loudness",
                                          formatter_class=argparse.RawTextHelpFormatter)
    aud_normalize.add_argument("input", type=lambda x: is_valid_file(aud_normalize, x))
    aud_normalize.add_argument("output")
    aud_normalize.add_argument("--target", type=float, default=-16.0, help="Target LUFS (default: -16)")
    def _aud_normalize(args):
        audio_ops.normalize_audio(args.input, args.output, target_loudness=args.target)
    aud_normalize.set_defaults(func=_aud_normalize)

    aud_fade = subparsers.add_parser("audio-fade", help="Apply fade in/out to audio",
                                     formatter_class=argparse.RawTextHelpFormatter)
    aud_fade.add_argument("input", type=lambda x: is_valid_file(aud_fade, x))
    aud_fade.add_argument("output")
    aud_fade.add_argument("--fade-in", type=float, help="Fade in duration (seconds)")
    aud_fade.add_argument("--fade-out", type=float, help="Fade out duration (seconds)")
    aud_fade.add_argument("--total-duration", type=float, help="Total duration (required for fade-out)")
    def _aud_fade(args):
        audio_ops.fade_audio(args.input, args.output, fade_in=args.fade_in,
                             fade_out=args.fade_out, total_duration=args.total_duration)
    aud_fade.set_defaults(func=_aud_fade)

    aud_resample = subparsers.add_parser("audio-resample", help="Resample audio",
                                         formatter_class=argparse.RawTextHelpFormatter)
    aud_resample.add_argument("input", type=lambda x: is_valid_file(aud_resample, x))
    aud_resample.add_argument("output")
    aud_resample.add_argument("-r", "--rate", type=int, default=44100, help="Sample rate (default: 44100)")
    def _aud_resample(args):
        audio_ops.resample_audio(args.input, args.output, sample_rate=args.rate)
    aud_resample.set_defaults(func=_aud_resample)

    aud_clean = subparsers.add_parser(
        "audio-clean",
        help="Clean up vocal audio (highpass + denoise + compress + loudnorm)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Single-pass ffmpeg vocal cleanup chain. Tuned for spoken or sung lead.

Stages (each independently skippable):
  1. highpass -- removes rumble, plosives, AC hum (default 80 Hz)
  2. afftdn   -- FFT noise reduction, removes hiss (default nf=-25)
  3. acompressor -- evens out dynamics
  4. loudnorm -- EBU R128 to -14 LUFS / -1 dBTP / LRA 11 (streaming target)

Examples:
  # Default chain (matches the canonical vocal-cleanup recipe)
  %(prog)s in.mp3 out.mp3

  # Deeper voice: drop the high-pass to 60 Hz
  %(prog)s in.mp3 out.mp3 --highpass 60

  # Hiss remaining? lower the noise floor
  %(prog)s in.mp3 out.mp3 --denoise -30

  # Sounds robotic / over-denoised? raise the floor or skip
  %(prog)s in.mp3 out.mp3 --denoise -20
  %(prog)s in.mp3 out.mp3 --no-denoise

  # Broadcast target instead of streaming
  %(prog)s in.mp3 out.mp3 --lufs -23 --true-peak -2.0"""
    )
    aud_clean.add_argument("input", type=lambda x: is_valid_file(aud_clean, x))
    aud_clean.add_argument("output")
    aud_clean.add_argument("--highpass", type=int, default=80,
                           help="High-pass cutoff in Hz (default: 80). Use 0 to disable.")
    aud_clean.add_argument("--no-highpass", action="store_true", help="Skip high-pass filter")
    aud_clean.add_argument("--denoise", type=float, default=-25.0,
                           help="afftdn noise floor in dB (default: -25)")
    aud_clean.add_argument("--no-denoise", action="store_true", help="Skip FFT denoise")
    aud_clean.add_argument("--no-compress", action="store_true", help="Skip compressor")
    aud_clean.add_argument("--threshold", type=float, default=-15.0,
                           help="Compressor threshold dB (default: -15)")
    aud_clean.add_argument("--ratio", type=float, default=3.0,
                           help="Compressor ratio (default: 3)")
    aud_clean.add_argument("--attack", type=float, default=5.0,
                           help="Compressor attack ms (default: 5)")
    aud_clean.add_argument("--release", type=float, default=50.0,
                           help="Compressor release ms (default: 50)")
    aud_clean.add_argument("--no-loudnorm", action="store_true", help="Skip loudness normalization")
    aud_clean.add_argument("--lufs", type=float, default=-14.0,
                           help="Target integrated loudness LUFS (default: -14)")
    aud_clean.add_argument("--true-peak", type=float, default=-1.0,
                           help="True peak ceiling dBTP (default: -1.0)")
    aud_clean.add_argument("--lra", type=float, default=11.0,
                           help="Loudness range target (default: 11)")
    aud_clean.add_argument("--codec", dest="audio_codec",
                           help="Audio codec (auto-detected from extension)")
    aud_clean.add_argument("--bitrate", help="Bitrate (e.g. 320k); overrides default VBR for mp3")
    def _aud_clean(args):
        highpass = None if args.no_highpass or args.highpass == 0 else args.highpass
        denoise = None if args.no_denoise else args.denoise
        audio_ops.clean_audio(
            args.input, args.output,
            highpass=highpass, denoise=denoise,
            compress=not args.no_compress,
            compress_threshold=args.threshold, compress_ratio=args.ratio,
            compress_attack=args.attack, compress_release=args.release,
            loudnorm=not args.no_loudnorm,
            target_lufs=args.lufs, true_peak=args.true_peak, lra=args.lra,
            audio_codec=args.audio_codec, bitrate=args.bitrate,
        )
    aud_clean.set_defaults(func=_aud_clean)

    aud_sep = subparsers.add_parser(
        "audio-separate",
        help="Separate audio into stems using a source-separation model (optional extra)",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Run a source-separation model on an audio file and write one
output file per stem to OUTPUT_DIR. Stem names come from the model.

Requires the 'separation' optional extra:
    uv sync --extra separation

Models are auto-downloaded on first use. List available models with:
    %(prog)s --list

Examples:
  # Vocals + instrumental (mel-band roformer)
  %(prog)s song.mp3 mel_band_roformer_kim_FT_by_aname-tommy.ckpt ./stems/

  # 4-stem (Demucs)
  %(prog)s song.mp3 htdemucs_ft.yaml ./stems/

  # Show available models
  %(prog)s --list"""
    )
    aud_sep.add_argument("input", nargs="?", help="Input audio file")
    aud_sep.add_argument("model", nargs="?", help="Model filename (see --list)")
    aud_sep.add_argument("output_dir", nargs="?", help="Directory to write stems into")
    aud_sep.add_argument("--list", action="store_true",
                         help="List all supported models and exit")
    def _aud_separate(args):
        from . import separation
        if args.list:
            try:
                for m in separation.list_models():
                    print(m)
            except ImportError as e:
                raise SystemExit(str(e))
            return
        if not (args.input and args.model and args.output_dir):
            raise SystemExit("audio-separate requires INPUT MODEL OUTPUT_DIR (or --list)")
        try:
            outputs = separation.separate_audio(args.input, args.model, args.output_dir)
        except ImportError as e:
            raise SystemExit(str(e))
        for p in outputs:
            print(p)
    aud_sep.set_defaults(func=_aud_separate)

    aud_info = subparsers.add_parser("audio-info", help="Show audio information",
                                     formatter_class=argparse.RawTextHelpFormatter)
    aud_info.add_argument("input", type=lambda x: is_valid_file(aud_info, x))
    def _aud_info(args):
        info_data = audio_ops.get_audio_info(args.input)
        print(orjson.dumps(info_data, option=orjson.OPT_INDENT_2).decode())
    aud_info.set_defaults(func=_aud_info)

    # =============================================================== DL-LIVE ==

    dl_live = subparsers.add_parser(
        "dl-live",
        help="Record a YouTube/HLS live broadcast",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Record a live broadcast via yt-dlp. Output is always mp4
(forcing .ts triggers a yt-dlp/ffmpeg ARG_MAX overflow on macOS with YouTube's
long signed URLs -- see vidtools/ytdlp.py:build_ytdlp_live_command).

Waits for the stream to start, records from the beginning by default, and
survives network blips via infinite fragment retries.

Stop the recording with Ctrl-C; the in-progress file remains playable.

Examples:
  %(prog)s "https://youtube.com/@channel/live" show.mp4
  %(prog)s "https://www.youtube.com/watch?v=<id>" capture.mp4 --from-now
"""
    )
    dl_live.add_argument("url", help="Live broadcast URL")
    dl_live.add_argument("output", nargs="?", default="live_recording.mp4",
                         help="Output path (default: live_recording.mp4). "
                              "Non-mp4 suffixes are coerced to .mp4.")
    dl_live.add_argument("--wait", type=int, default=60,
                         help="Seconds between polls while waiting for the stream "
                              "to start (default: 60). 0 = fail if not live now.")
    dl_live.add_argument("--no-wait", action="store_true",
                         help="Equivalent to --wait 0.")
    dl_live.add_argument("--from-now", action="store_true",
                         help="Join at live edge instead of recording from start.")

    def _dl_live(args):
        out = Path(args.output)
        if out.suffix.lower() != ".mp4":
            print(f"coercing output {out} -> {out.with_suffix('.mp4')} (mp4 only)",
                  file=sys.stderr)
            out = out.with_suffix(".mp4")
        wait = 0 if args.no_wait else args.wait
        final = ytdlp.download_live(
            url=args.url,
            output_path=str(out),
            wait_interval=wait,
            from_start=not args.from_now,
        )
        print(final)
    dl_live.set_defaults(func=_dl_live)

    # ============================================================== PIPELINE ==

    pipeline = subparsers.add_parser(
        "pipeline",
        help="Chain multiple video operations into a single ffmpeg invocation",
        formatter_class=argparse.RawTextHelpFormatter,
        description="""Chain video operations. All steps are combined into one
ffmpeg command -- no intermediate files, no double re-encoding.

Examples:
  # Cut, resize, and re-encode in one pass
  %(prog)s in.mp4 out.mp4 cut --start 60 --end 90 resize -p 0.5 convert --crf 20

  # Crop then rotate
  %(prog)s in.mp4 out.mp4 crop -W 800 -H 600 -x 100 -y 50 rotate -r 90

Supported steps: cut, resize, crop, rotate, sanitize, subtitles, convert.
NOT supported in pipeline: concat, slideshow, extract-*, add-audio, preset.

Step order is preserved in the filter chain. cut input args are pulled to
before the input file regardless of position. convert is always terminal."""
    )
    pipeline.add_argument("input", help="Input video file")
    pipeline.add_argument("output", help="Output video file")
    pipeline.add_argument(
        "steps", nargs=argparse.REMAINDER,
        help="Pipeline steps: <op> [args...] <op> [args...] ..."
    )
    def _pipeline(args):
        from .pipeline import Pipeline
        steps = _parse_pipeline_steps(args.steps)
        if not steps:
            raise SystemExit("pipeline requires at least one step")
        if not _detect_kind(args.input) == "video":
            raise SystemExit("pipeline only supports video inputs in v1")
        Pipeline(args.input, args.output, steps).run()
    pipeline.set_defaults(func=_pipeline)

    # ==========================================================================

    # The return statement should be the VERY LAST thing in the function
    return parser


# --- Pipeline argv splitter ---

_PIPELINE_OP_NAMES = {"cut", "resize", "crop", "rotate", "sanitize", "subtitles", "convert"}


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
        p.add_argument("--vcodec", dest="vcodec")
        p.add_argument("--acodec", dest="acodec")
        p.add_argument("--vbitrate", dest="vbitrate")
        p.add_argument("--abitrate", dest="abitrate")
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
        j = i + 1
        while j < len(argv) and argv[j] not in _PIPELINE_OP_NAMES:
            j += 1
        step_args = _make_step_parser(op).parse_args(argv[i + 1:j])
        # Strip None values so the pipeline validation logic sees clean dicts
        params = {k: v for k, v in vars(step_args).items() if v is not None}
        steps.append(PipelineStep(op, params))
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
    if args.command:
        with progress_context(_make_cli_sink()):
            args.func(args)
    else:
        if _parser is not None:
            _parser.print_help()


def main():
    """Main entry point for the CLI."""
    from .progress import FfmpegError
    if not check_ffmpeg_installed():
        sys.exit("Error: ffmpeg not found in PATH. Please install ffmpeg first.")
    try:
        handle_command(parse_arguments())
    except FfmpegError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()

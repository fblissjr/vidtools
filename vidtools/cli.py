import argparse
import sys
from .utils import is_valid_file, check_ffmpeg_installed  # helper for path checks
from . import main as main_module  # command handlers live here


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
    resize_parser.set_defaults(func=main_module.resize_video_handler)

    # ---------------------------------------------------------------- sanitize
    sanitize = subparsers.add_parser(
        "sanitize",
        help="Auto‑crop bottom banner, add light noise, strip metadata/SEI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sanitize.add_argument("input",  type=lambda x: is_valid_file(sanitize, x))
    sanitize.add_argument("output")
    sanitize.add_argument(
        "--limit", type=int, default=24, help="cropdetect limit (default 24)"
    )
    sanitize.add_argument(
        "--noise", type=int, default=6, help="Noise amplitude 0‑100 (default 6)"
    )
    sanitize.add_argument(
        "--extra-bottom",
        type=int,
        default=0,
        metavar="PX",
        help="Crop PX more pixels off the bottom *after* auto‑detect",
    )
    sanitize.add_argument(
        "--manual-crop",
        metavar="EXPR",
        default=None,
        help="Explicit crop expression (e.g. 'iw:floor(ih*0.9/2)*2:0:0'). "
        "If set, auto‑detection is skipped and this crop is used verbatim.",
    )
    sanitize.add_argument(
        "--crf", type=int, default=22, help="x264 CRF quality (default 22)"
    )
    sanitize.add_argument(
        "--aac",
        action="store_true",
        help="Re‑encode audio to AAC 128 kb/s (default: copy)",
    )
    sanitize.add_argument("--no-audio", action="store_true",
                          help="Strip audio stream entirely")
    sanitize.set_defaults(func=main_module.sanitize_video_handler)

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
        help="Try to fix audio sync issues (adds -async 1)"
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
    cut.set_defaults(func=main_module.cut_video_handler)

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
    # Time range options
    convert.add_argument("--start", dest="start_time", help="Start time")
    convert.add_argument("--end", dest="end_time", help="End time")
    convert.add_argument("--duration", help="Duration")
    convert.set_defaults(func=main_module.convert_format_handler)

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
    extract_audio.set_defaults(func=main_module.extract_audio_handler)

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
    extract_frames.set_defaults(func=main_module.extract_frames_handler)

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
    concat.set_defaults(func=main_module.concatenate_videos_handler)

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
    crop.set_defaults(func=main_module.crop_video_handler)

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
    rotate.set_defaults(func=main_module.rotate_video_handler)

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
    subtitles.set_defaults(func=main_module.add_subtitles_handler)

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
    info.set_defaults(func=main_module.get_video_info_handler)

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
    apply.set_defaults(func=main_module.apply_preset_handler)

    # List presets
    list_p = preset_sub.add_parser("list", help="List available presets")
    list_p.set_defaults(func=main_module.list_presets_handler)

    # Delete preset
    delete = preset_sub.add_parser("delete", help="Delete a preset")
    delete.add_argument("name", help="Preset name")
    delete.set_defaults(func=main_module.delete_preset_handler)

    # Edit preset
    edit = preset_sub.add_parser("edit", help="Edit preset file")
    edit.add_argument("name", help="Preset name")
    edit.set_defaults(func=main_module.edit_preset_handler)

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
    slideshow.set_defaults(func=main_module.slideshow_handler)
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
    add_audio_parser.set_defaults(func=main_module.add_audio_handler)

    srt_parser = subparsers.add_parser(
        "srt-from-transcript",
        help="Convert a timed transcript to a .srt subtitle file",
        formatter_class=argparse.RawTextHelpFormatter,
        description="Converts a transcript file with simple second-based timestamps"
    )
    srt_parser.add_argument("input", type=lambda x: is_valid_file(srt_parser, x),
                            help="Input transcript text file")
    srt_parser.add_argument("output", help="Output .srt subtitle file")
    srt_parser.set_defaults(func=main_module.create_srt_from_transcript_handler)
    # ==========================================================================

    # The return statement should be the VERY LAST thing in the function
    return parser

# singleton parser -----------------------------------------------------------
_parser: argparse.ArgumentParser | None = None


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    global _parser
    if _parser is None:
        _parser = setup_argparse()
    return _parser.parse_args(argv)


def handle_command(args: argparse.Namespace) -> None:
    if args.command:
        args.func(args)
    else:
        _parser.print_help()


def main():
    """Main entry point for the CLI."""
    if not check_ffmpeg_installed():
        sys.exit("Error: ffmpeg not found in PATH. Please install ffmpeg first.")
    handle_command(parse_arguments())

if __name__ == "__main__":
    main()

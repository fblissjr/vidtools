import argparse
from typing import Callable, Any
from utils import is_valid_file
import main  # Import main module to call handler functions

def setup_argparse() -> argparse.ArgumentParser:
    """Sets up the argparse parser with subcommands and arguments."""
    parser = argparse.ArgumentParser(description="FFmpeg Command Line Tool - Advanced Video/Audio Operations with Presets.",
                                     formatter_class=argparse.RawTextHelpFormatter)

    subparsers = parser.add_subparsers(title="commands", dest="command", help="Available ffmpeg operations")

    # --- Resize Command ---
    resize_parser = subparsers.add_parser("resize", help="Resize video resolution", formatter_class=argparse.RawTextHelpFormatter)
    resize_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(resize_parser, x))
    resize_parser.add_argument("output", help="Output resized video file")
    resize_group = resize_parser.add_mutually_exclusive_group(required=True)
    resize_group.add_argument("-p", "--percentage", type=float, help="Resize by percentage (e.g., 0.5 for 50%)")
    resize_group.add_argument("-W", "--width", type=int, help="Desired output width in pixels")
    resize_group.add_argument("-H", "--height", type=int, help="Desired output height in pixels")
    resize_parser.add_argument("--algorithm", default="lanczos", choices=["neighbor", "fast_bilinear", "bilinear", "bicubic", "experimental", "area", "bicubiclina", "gauss", "sinc", "lanczos", "spline"], help="Scaling algorithm (default: lanczos)")
    resize_parser.set_defaults(func=main.resize_video_handler) # Call handler in main.py

    # --- Convert Command ---
    convert_parser = subparsers.add_parser("convert", help="Convert video to different formats with codec options and time clipping", formatter_class=argparse.RawTextHelpFormatter)
    convert_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(convert_parser, x))
    convert_parser.add_argument("output", help="Output converted file")
    convert_parser.add_argument("format", choices=["gif", "mp3", "webm", "avi", "mp4"], help="Target format (gif, mp3, webm, avi, mp4)")
    convert_parser.add_argument("--vcodec", help="Video codec")
    convert_parser.add_argument("--acodec", help="Audio codec")
    convert_parser.add_argument("--vbitrate", help="Video bitrate (e.g., 2000k, 5M)")
    convert_parser.add_argument("--abitrate", help="Audio bitrate (e.g., 128k, 192k)")
    convert_parser.add_argument("--quality", type=str, help="Quality scale (e.g., CRF value for x264/x265, qscale for others)")
    time_group_convert = convert_parser.add_mutually_exclusive_group()
    time_group_convert.add_argument("--ss", "--start_time", dest="start_time", help="Start time (e.g., 00:00:10 or 10s)")
    time_group_convert.add_argument("--to", "--end_time", dest="end_time", help="End time (e.g., 00:01:00 or 60s)")
    time_group_convert.add_argument("-t", "--duration", help="Duration (e.g., 30s)")
    convert_parser.set_defaults(func=main.convert_format_handler) # Call handler in main.py

    # --- Extract Audio Command ---
    extract_audio_parser = subparsers.add_parser("extract_audio", help="Extract audio from video with time clipping", formatter_class=argparse.RawTextHelpFormatter)
    extract_audio_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(extract_audio_parser, x))
    extract_audio_parser.add_argument("output", help="Output audio file (e.g., audio.mp3)")
    extract_audio_parser.add_argument("--aformat", default="copy", help="Audio format/codec (default: copy)")
    time_group_audio = extract_audio_parser.add_mutually_exclusive_group()
    time_group_audio.add_argument("--ss", "--start_time", dest="start_time", help="Start time (e.g., 00:00:10 or 10s)")
    time_group_audio.add_argument("--to", "--end_time", dest="end_time", help="End time (e.g., 00:01:00 or 60s)")
    time_group_audio.add_argument("-t", "--duration", help="Duration (e.g., 30s)")
    extract_audio_parser.set_defaults(func=main.extract_audio_handler) # Call handler in main.py

    # --- Extract Frames Command ---
    extract_frames_parser = subparsers.add_parser("extract_frames", help="Extract frames from video with time clipping", formatter_class=argparse.RawTextHelpFormatter)
    extract_frames_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(extract_frames_parser, x))
    extract_frames_parser.add_argument("output_pattern", help="Output frame file pattern (e.g., frame%04d.jpg)")
    extract_frames_parser.add_argument("-r", "--rate", type=float, default=1, help="Frame rate (frames per second, default=1)")
    extract_frames_parser.add_argument("--iformat", default="image2", help="Image format (e.g., image2, image2pipe)")
    time_group_frames = extract_frames_parser.add_mutually_exclusive_group()
    time_group_frames.add_argument("--ss", "--start_time", dest="start_time", help="Start time (e.g., 00:00:10 or 10s)")
    time_group_frames.add_argument("--to", "--end_time", dest="end_time", help="End time (e.g., 00:01:00 or 60s)")
    time_group_frames.add_argument("-t", "--duration", help="Duration (e.g., 30s)")
    extract_frames_parser.set_defaults(func=main.extract_frames_handler) # Call handler in main.py

    # --- Concatenate Command ---
    concat_parser = subparsers.add_parser("concat", help="Concatenate multiple video files", formatter_class=argparse.RawTextHelpFormatter)
    concat_parser.add_argument("output", help="Output concatenated video file")
    concat_parser.add_argument("inputs", nargs='+', help="Input video files to concatenate (in order)", type=lambda x: is_valid_file(concat_parser, x))
    concat_parser.set_defaults(func=main.concatenate_videos_handler) # Call handler in main.py

    # --- Crop Command ---
    crop_parser = subparsers.add_parser("crop", help="Crop a video", formatter_class=argparse.RawTextHelpFormatter)
    crop_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(crop_parser, x))
    crop_parser.add_argument("output", help="Output cropped video file")
    crop_parser.add_argument("width", type=int, help="Crop width")
    crop_parser.add_argument("height", type=int, help="Crop height")
    crop_parser.add_argument("x", type=int, help="Crop X offset (left)")
    crop_parser.add_argument("y", type=int, help="Crop Y offset (top)")
    crop_parser.set_defaults(func=main.crop_video_handler) # Call handler in main.py

    # --- Video Info Command ---
    info_parser = subparsers.add_parser("info", help="Get video information", formatter_class=argparse.RawTextHelpFormatter)
    info_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(info_parser, x))
    info_parser.set_defaults(func=main.get_video_info_handler) # Call handler in main.py

    # --- Subtitles Command ---
    subtitles_parser = subparsers.add_parser("subtitles", help="Add subtitles to a video (burn-in)", formatter_class=argparse.RawTextHelpFormatter)
    subtitles_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(subtitles_parser, x))
    subtitles_parser.add_argument("output", help="Output video with subtitles")
    subtitles_parser.add_argument("subtitles_file", help="Subtitles file (.srt, .ass, etc.)", type=lambda x: is_valid_file(subtitles_parser, x))
    subtitles_parser.set_defaults(func=main.add_subtitles_handler) # Call handler in main.py

    # --- Rotate Command ---
    rotate_parser = subparsers.add_parser("rotate", help="Rotate a video", formatter_class=argparse.RawTextHelpFormatter)
    rotate_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(rotate_parser, x))
    rotate_parser.add_argument("output", help="Output rotated video")
    rotate_parser.add_argument("rotation", choices=["90", "180", "270"], help="Rotation angle in degrees (90, 180, 270)")
    rotate_parser.set_defaults(func=main.rotate_video_handler) # Call handler in main.py

    # --- Apply Preset Command ---
    preset_parser = subparsers.add_parser("preset", help="Apply a predefined preset for common tasks", formatter_class=argparse.RawTextHelpFormatter)
    preset_parser.add_argument("input", help="Input video file", type=lambda x: is_valid_file(preset_parser, x))
    preset_parser.add_argument("output", help="Output file")
    preset_parser.add_argument("preset_name", help=f"Name of the preset to apply. Available presets: {', '.join(main.PRESETS.keys())}") # Access presets from main
    preset_parser.set_defaults(func=main.apply_preset_handler) # Call handler in main.py

    # --- List Presets Command ---
    list_presets_parser = subparsers.add_parser("list_presets", help="List available presets and their descriptions", formatter_class=argparse.RawTextHelpFormatter)
    list_presets_parser.set_defaults(func=main.list_presets_handler) # Call handler in main.py

    # --- Save Preset Command ---
    save_preset_parser = subparsers.add_parser("save_preset", help="Save current command options as a preset", formatter_class=argparse.RawTextHelpFormatter)
    save_preset_parser.add_argument("preset_name", help="Name to save the preset as")
    save_preset_parser.set_defaults(func=main.save_preset_handler) # Call handler in main.py

    # --- Delete Preset Command ---
    delete_preset_parser = subparsers.add_parser("delete_preset", help="Delete a preset", formatter_class=argparse.RawTextHelpFormatter)
    delete_preset_parser.add_argument("preset_name", help="Name of the preset to delete")
    delete_preset_parser.set_defaults(func=main.delete_preset_handler) # Call handler in main.py

    # --- Edit Preset Command ---
    edit_preset_parser = subparsers.add_parser("edit_preset", help="Edit a preset by manually editing the JSON file", formatter_class=argparse.RawTextHelpFormatter)
    edit_preset_parser.add_argument("preset_name", help="Name of the preset to edit")
    edit_preset_parser.set_defaults(func=main.edit_preset_handler) # Call handler in main.py

    return parser

def handle_command(args: argparse.Namespace):
    """Handles the execution of the command based on parsed arguments."""
    if args.command:
        args.func(args) # Call the handler function set by subparsers
    else:
        arg_parser.print_help() # Print help if no command is given

# Global parser instance for potential TUI use (can be refined later)
arg_parser = setup_argparse()

def parse_arguments(command_line_args=None):
    """Parses command line arguments using argparse."""
    return arg_parser.parse_args(command_line_args) # Allows passing args for testing or TUI

if __name__ == "__main__":
    args = parse_arguments()
    handle_command(args)
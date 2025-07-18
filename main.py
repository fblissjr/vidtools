import sys
import subprocess
import re
import cli                                          # CLI dispatcher
from utils import run_ffmpeg_command, check_ffmpeg_installed, logger
import presets
import json
from typing import Sequence

# re-export presets for CLI help
PRESETS = presets.get_presets()

save_presets_command = presets.save_preset_command
delete_preset_command = presets.delete_preset
edit_preset_command = presets.edit_preset_command
list_presets_command = presets.list_presets

# Re-export presets for cli and main access. Consider if direct import from presets.py in cli is better
PRESETS = presets.get_presets()
save_presets_command = presets.save_preset_command
delete_preset_command = presets.delete_preset
edit_preset_command = presets.edit_preset_command
list_presets_command = presets.list_presets # Direct function access


# --- FFmpeg Function Wrappers (Orchestrators) ---

def resize_video_handler(args):
    percentage = args.percentage if hasattr(args, 'percentage') and args.percentage else None
    width = args.width if hasattr(args, 'width') and args.width else None
    height = args.height if hasattr(args, 'height') and args.height else None
    algorithm = args.algorithm
    input_file = args.input
    output_file = args.output
    resize_video(input_file, output_file, percentage=percentage, width=width, height=height, algorithm=algorithm)

def convert_format_handler(args):
    input_file = args.input
    output_file = args.output
    format_type = args.format
    video_codec = args.vcodec
    audio_codec = args.acodec
    video_bitrate = args.vbitrate
    audio_bitrate = args.abitrate
    quality_scale = args.quality
    start_time = args.start_time
    end_time = args.end_time
    duration = args.duration
    convert_format(input_file, output_file, format_type, video_codec, audio_codec, video_bitrate, audio_bitrate, quality_scale, start_time, end_time, duration)

def extract_audio_handler(args):
    input_file = args.input
    output_file = args.output
    audio_format = args.aformat
    start_time = args.start_time
    end_time = args.end_time
    duration = args.duration
    extract_audio(input_file, output_file, audio_format, start_time, end_time, duration)

def extract_frames_handler(args):
    input_file = args.input
    output_pattern = args.output_pattern
    frame_rate = args.rate
    image_format = args.iformat
    start_time = args.start_time
    end_time = args.end_time
    duration = args.duration
    extract_frames(input_file, output_pattern, frame_rate, image_format, start_time, end_time, duration)

def concatenate_videos_handler(args):
    input_files = args.inputs
    output_file = args.output
    concatenate_videos(input_files, output_file)

def crop_video_handler(args):
    input_file = args.input
    output_file = args.output
    width = args.width
    height = args.height
    x = args.x
    y = args.y
    crop_video(input_file, output_file, width, height, x, y)

def get_video_info_handler(args):
    input_file = args.input
    get_video_info(input_file)

def add_subtitles_handler(args):
    input_file = args.input
    output_file = args.output
    subtitles_file = args.subtitles_file
    add_subtitles(input_file, output_file, subtitles_file)

def rotate_video_handler(args):
    input_file = args.input
    output_file = args.output
    rotation = args.rotation
    rotate_video(input_file, output_file, rotation)

def apply_preset_handler(args):
    input_file = args.input
    output_file = args.output
    preset_name = args.preset_name
    apply_preset(input_file, output_file, preset_name)

def list_presets_handler(args): # args not used in list_presets
    list_presets_command() # Call the command function from presets.py

# --- Core FFmpeg Functions (Orchestrated by Handlers) ---

def resize_video(input_file, output_file, percentage=None, width=None, height=None, algorithm="lanczos"):
    """Resizes a video using ffmpeg scale filter."""
    if percentage:
        scale_filter = f"scale=iw*{percentage}:ih*{percentage}:flags={algorithm}"
    elif width and height:
        scale_filter = f"scale={width}:{height}:flags={algorithm}"
    elif width:
        scale_filter = f"scale={width}:-1:flags={algorithm}"
    elif height:
        scale_filter = f"scale=-1:{height}:flags={algorithm}"
    else:
        print("Error: You must specify either a percentage or width/height for resizing.")
        return

    command = ["ffmpeg", "-i", input_file, "-vf", scale_filter, output_file]
    run_ffmpeg_command(command) # Call run_ffmpeg_command from utils

def convert_format(input_file, output_file, format_type, video_codec=None, audio_codec=None, video_bitrate=None, audio_bitrate=None, quality_scale=None, start_time=None, end_time=None, duration=None):
    """Converts video format using ffmpeg."""
    command = ["ffmpeg", "-i", input_file]
    if start_time: command.extend(["-ss", str(start_time)])
    if end_time: command.extend(["-to", str(end_time)])
    if duration: command.extend(["-t", str(duration)])

    if format_type == "gif":
        command.extend(["-vf", "palettegen=stats_mode=diff[pal],[0:v][pal]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"])
    elif format_type == "mp3":
        command.extend(["-vn", "-ab", audio_bitrate or "128k", "-ar", "44100"])
    elif format_type == "webm":
        command.extend(["-c:v", video_codec or "libvpx-vp9", "-crf", quality_scale or "30", "-b:v", video_bitrate or "0", "-b:a", audio_bitrate or "128k"])
    elif format_type == "avi":
        command.extend(["-c:v", video_codec or "libxvid", "-qscale:v", quality_scale or "5", "-c:a", audio_codec or "libmp3lame"])
    elif format_type == "mp4":
        if video_codec: command.extend(["-c:v", video_codec])
        if audio_codec: command.extend(["-c:a", audio_codec])
        if video_bitrate: command.extend(["-b:v", video_bitrate])
        if audio_bitrate: command.extend(["-b:a", audio_bitrate])
        if quality_scale and video_codec in ("libx264", "libx265"):
            command.extend(["-crf", quality_scale])
    else:
        print(f"Error: Unsupported format type: {format_type}")
        return

    command.append(output_file)
    run_ffmpeg_command(command) # Call run_ffmpeg_command from utils

def extract_audio(input_file, output_file, audio_format="copy", start_time=None, end_time=None, duration=None):
    """Extracts audio from video using ffmpeg."""
    command = ["ffmpeg", "-i", input_file]
    if start_time: command.extend(["-ss", str(start_time)])
    if end_time: command.extend(["-to", str(end_time)])
    if duration: command.extend(["-t", str(duration)])
    command.extend(["-vn", "-acodec", audio_format, output_file])
    run_ffmpeg_command(command) # Call run_ffmpeg_command from utils

def extract_frames(input_file, output_pattern, frame_rate=1, image_format="image2", start_time=None, end_time=None, duration=None):
    """Extracts frames from video using ffmpeg."""
    command = ["ffmpeg", "-i", input_file]
    if start_time: command.extend(["-ss", str(start_time)])
    if end_time: command.extend(["-to", str(end_time)])
    if duration: command.extend(["-t", str(duration)])
    command.extend(["-r", str(frame_rate), "-f", image_format, output_pattern])
    run_ffmpeg_command(command) # Call run_ffmpeg_command from utils

def concatenate_videos(input_files, output_file):
    """Concatenates video files using ffmpeg concat protocol."""
    list_file_path = "concat_list.txt"
    try:
        with open(list_file_path, "w") as f:
            for file in input_files:
                f.write(f"file '{file}'\n")
        command = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file_path, "-c", "copy", output_file]
        run_ffmpeg_command(command) # Call run_ffmpeg_command from utils
    except Exception as e:
        print(f"Error creating or using list file: {e}")
    finally:
        if os.path.exists(list_file_path):
            os.remove(list_file_path)

def crop_video(input_file, output_file, width, height, x, y):
    """Crops a video using ffmpeg crop filter."""
    crop_filter = f"crop={width}:{height}:{x}:{y}"
    command = ["ffmpeg", "-i", input_file, "-vf", crop_filter, output_file]
    run_ffmpeg_command(command) # Call run_ffmpeg_command from utils

def get_video_info(input_file):
    """Gets video information using ffprobe."""
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", input_file]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"ffprobe command failed with error code: {e.returncode}")
        print(f"Error Output:\n{e.stderr}")
        exit(1)
    except FileNotFoundError:
        print("Error: ffprobe not found. Please ensure ffmpeg is installed and in your PATH.")
        exit(1)

def add_subtitles(input_file, output_file, subtitles_file):
    """Adds subtitles to video using ffmpeg subtitles filter."""
    if not os.path.isfile(subtitles_file):
        print(f"Error: Subtitle file '{subtitles_file}' not found.")
        return
    command = ["ffmpeg", "-i", input_file, "-vf", f"subtitles={subtitles_file}", output_file]
    run_ffmpeg_command(command) # Call run_ffmpeg_command from utils

def rotate_video(input_file, output_file, rotation):
    """Rotates video using ffmpeg transpose filter."""
    rotation_values = {
        "90": "1",   # 90 degrees clockwise
        "180": "2,transpose=2", # 180 degrees (apply transpose=2 twice)
        "270": "3"  # 270 degrees clockwise (or 90 counter-clockwise)
    }
    if rotation not in rotation_values:
        print(f"Error: Invalid rotation value. Choose from 90, 180, 270 degrees.")
        return

    rotate_filter = f'"{rotation_values[rotation]}"'
    command = ["ffmpeg", "-i", input_file, "-vf", rotate_filter, output_file]
    run_ffmpeg_command(command) # Call run_ffmpeg_command from utils

def apply_preset(input_file, output_file, preset_name):
    """Applies a preset configuration."""
    all_presets = presets.get_presets() # Get presets from presets.py
    if preset_name not in all_presets:
        print(f"Error: Preset '{preset_name}' not found. Available presets are: {', '.join(all_presets.keys())}")
        return

    preset = all_presets[preset_name]
    format_type = preset.get("format")

    if format_type:
        convert_format(input_file, output_file, format_type,
                       video_codec=preset.get("vcodec"), audio_codec=preset.get("acodec"),
                       video_bitrate=preset.get("vbitrate"), audio_bitrate=preset.get("abitrate"),
                       quality_scale=preset.get("quality"))
    elif "resize_percentage" in preset:
        resize_video(input_file, output_file, percentage=preset["resize_percentage"])
    else:
        print(f"Error: Preset '{preset_name}' is not fully defined or recognized.")

def save_preset_handler(args):
    """Saves a preset by parsing the current command line."""
    preset_name = args.preset_name

    # 1. Get the current command line arguments (excluding the script name itself)
    current_command_line = sys.argv[1:]

    if not current_command_line:
        print("Error: No command specified to save as preset.")
        return

    command_name = current_command_line[0] # e.g., "resize", "convert"

    # 2. Get the argparse parser from cli.py
    try:
        arg_parser = cli.get_parser()  # Use function call instead of direct attribute access
    except AttributeError:
        print("Error: Unable to access command line parser.")
        return

    try:
        # 3. Re-parse the command line, but only the relevant part for the command
        try:
            command_args = arg_parser.parse_args(current_command_line) # Parse based on the *entire* current command line
        except SystemExit as e: # Catch argparse's exit on error
            if e.code == 2: # Exit code 2 usually indicates parsing error
                print("Error: Invalid command line for saving preset. Please check your command and options before using 'save_preset'. Run with '-h' or '--help' for usage.")
                return
            else:
                raise # Re-raise other SystemExit exceptions

        # 4. Extract preset data based on the command name and parsed arguments
        preset_data = {}
        description_parts = [] # For building more informative descriptions

        preset_data = _extract_preset_data_for_command(command_name, command_args, description_parts)

        preset_data["description"] = ", ".join(description_parts) or preset_name # Use preset name as description if no specific options

        # 5. Validation (basic example - you can expand this)
        if not preset_data.get("description"):
            print("Error: Could not generate a description for the preset. Saving cancelled.")
            return

        # 6. Save the preset
        if preset_data:
            is_valid = validate_preset_data(preset_data, command_name) # Basic validation function (below)
            if is_valid:
                save_presets_command(preset_name, preset_data)
            else:
                print("Error: Preset data validation failed. Preset not saved.")
        else:
            print("Error: Could not extract any preset settings from the command line.")

    except Exception as e:
        print(f"Error processing 'save_preset' command: {e}")
        logger.exception("Error in save_preset_handler")


def _extract_preset_data_for_command(command_name, command_args, description_parts):
    """Extract preset data based on command name and arguments."""
    preset_data = {}

    if command_name == "resize":
        preset_data = _extract_resize_preset_data(command_args, description_parts)
    elif command_name == "convert":
        preset_data = _extract_convert_preset_data(command_args, description_parts)
    elif command_name == "extract_audio":
        preset_data = _extract_audio_preset_data(command_args, description_parts)
    elif command_name == "extract_frames":
        preset_data = _extract_frames_preset_data(command_args, description_parts)
    elif command_name == "crop":
        preset_data = _extract_crop_preset_data(command_args, description_parts)
    elif command_name == "rotate":
        preset_data = _extract_rotate_preset_data(command_args, description_parts)
    elif command_name == "concatenate":
        preset_data["description"] = "Concatenate Videos"
    elif command_name == "subtitles":
        preset_data["description"] = "Add Subtitles (burn-in)"
    else:
        print(f"Warning: 'save_preset' is not fully implemented for command: {command_name}. Only basic presets may be saved.")
        description_parts.append(f"Preset for {command_name} (basic)")

    return preset_data


def _extract_resize_preset_data(command_args, description_parts):
    """Extract preset data for resize command."""
    preset_data = {}

    if command_args.percentage is not None:
        preset_data["resize_percentage"] = command_args.percentage
        description_parts.append(f"Resize by {command_args.percentage*100}%")
    elif command_args.width is not None:
        preset_data["width"] = command_args.width
        preset_data["height"] = command_args.height or -1
        description_parts.append(f"Resize to {command_args.width}x{command_args.height if command_args.height else 'auto'}")

    preset_data["algorithm"] = command_args.algorithm
    description_parts.append(f"Algorithm: {command_args.algorithm}")

    return preset_data


def _extract_convert_preset_data(command_args, description_parts):
    """Extract preset data for convert command."""
    preset_data = {}

    preset_data["format"] = command_args.format
    description_parts.append(f"Convert to {command_args.format}")

    if command_args.vcodec:
        preset_data["vcodec"] = command_args.vcodec
        description_parts.append(f"Video Codec: {command_args.vcodec}")
    if command_args.acodec:
        preset_data["acodec"] = command_args.acodec
        description_parts.append(f"Audio Codec: {command_args.acodec}")
    if command_args.vbitrate:
        preset_data["vbitrate"] = command_args.vbitrate
        description_parts.append(f"Video Bitrate: {command_args.vbitrate}")
    if command_args.abitrate:
        preset_data["abitrate"] = command_args.abitrate
        description_parts.append(f"Audio Bitrate: {command_args.abitrate}")
    if command_args.quality:
        preset_data["quality"] = command_args.quality
        description_parts.append(f"Quality: {command_args.quality}")

    return preset_data


def _extract_audio_preset_data(command_args, description_parts):
    """Extract preset data for extract_audio command."""
    preset_data = {}
    preset_data["aformat"] = command_args.aformat
    description_parts.append(f"Extract Audio (Format: {command_args.aformat})")
    return preset_data


def _extract_frames_preset_data(command_args, description_parts):
    """Extract preset data for extract_frames command."""
    preset_data = {}
    preset_data["rate"] = command_args.rate
    preset_data["iformat"] = command_args.iformat
    description_parts.append(f"Extract Frames (Rate: {command_args.rate}, Format: {command_args.iformat})")
    return preset_data


def _extract_crop_preset_data(command_args, description_parts):
    """Extract preset data for crop command."""
    preset_data = {}
    preset_data["width"] = command_args.width
    preset_data["height"] = command_args.height
    preset_data["x"] = command_args.x
    preset_data["y"] = command_args.y
    description_parts.append(f"Crop to {command_args.width}x{command_args.height} at {command_args.x},{command_args.y}")
    return preset_data


def _extract_rotate_preset_data(command_args, description_parts):
    """Extract preset data for rotate command."""
    preset_data = {}
    preset_data["rotation"] = command_args.rotation
    description_parts.append(f"Rotate {command_args.rotation} degrees")
    return preset_data


def validate_preset_data(preset_data, command_name):
    """Basic validation of preset data (example, expand as needed)."""
    if not isinstance(preset_data, dict):
        logger.warning("Preset data is not a dictionary", data=preset_data)
        return False
    if not preset_data.get("description"):
        logger.warning("Preset data has no description", data=preset_data)
        return False

    if command_name == "resize":
        if not any(k in preset_data for k in ["resize_percentage", "width"]): # Need at least resize_percentage or width
            logger.warning("Resize preset missing resize parameters", data=preset_data)
            return False
        if "algorithm" in preset_data and not preset_data["algorithm"] in ["neighbor", "fast_bilinear", "bilinear", "bicubic", "experimental", "area", "bicubiclina", "gauss", "sinc", "lanczos", "spline"]:
            logger.warning("Invalid resize algorithm in preset", algorithm=preset_data["algorithm"])
            return False

    elif command_name == "convert":
        if not preset_data.get("format"):
            logger.warning("Convert preset missing format", data=preset_data)
            return False
        if "vcodec" in preset_data and not preset_data["vcodec"]: # Example - check if vcodec is not empty string if present
            logger.warning("Convert preset has empty vcodec", data=preset_data)
            return False

    # Add more validation for other command types as needed

    logger.debug("Preset data validated successfully", command=command_name, data=preset_data)
    return True

# ──────────────────────────── sanitize handler ───────────────────────────────
def sanitize_video_handler(args) -> None:
    sanitize_video(
        input_file=args.input,
        output_file=args.output,
        limit=args.limit,
        noise=args.noise,
        crf=args.crf,
        extra_bottom=args.extra_bottom,
        manual_crop=args.manual_crop,
        audio_mode=("none" if args.no_audio else "aac" if args.aac else "copy"),
    )

# ───────────────────────────── core implementation ───────────────────────────
def _probe_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) of the first video stream via ffprobe JSON."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", path],
        capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)["streams"][0]
    return int(info["width"]), int(info["height"])


def sanitize_video(
    *,
    input_file: str,
    output_file: str,
    limit: int = 24,
    noise: int = 6,
    crf: int = 22,
    extra_bottom: int = 0,
    manual_crop: str | None = None,
    audio_mode: str = "copy",  # "copy" | "aac" | "none"
) -> None:
    """
    1. Detect bottom banner with cropdetect (first ≈12 s).
    2. Trim only height (keep full width), add light grain.
    3. Strip metadata & SEI, preserve/copy/re-encode audio as requested.
    """
    # If a manual crop expression is provided, use it directly
    if manual_crop:
        crop_expr = manual_crop
    else:
        # ── auto‑detect banner with cropdetect ──────────────────────────
        detect_cmd: Sequence[str] = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            input_file,
            "-vf",
            f"cropdetect=limit={limit}:round=2:reset=0",
            "-frames:v",
            "300",
            "-f",
            "null",
            "-",
        ]
        detect = subprocess.run(detect_cmd, stderr=subprocess.PIPE, text=True)
        matches = re.findall(r"crop=([0-9:]+)", detect.stderr)

        _, in_h = _probe_dimensions(input_file)
        if not matches:
            logger.warning("cropdetect found no banner; keeping full frame")
            crop_expr = "iw:ih:0:0"
        else:
            # Take last (safest) suggestion, keep full width
            _, h_str, _, _ = matches[-1].split(":")
            new_h = max(2, (int(h_str) - extra_bottom)) & ~1
            crop_expr = f"iw:{new_h}:0:0"
            if new_h == in_h:
                logger.info("No vertical crop needed (banner not detected)")

    vf_chain = f"crop={crop_expr},noise=alls={noise}:allf=t+u"

    # 2) build ffmpeg command
    cmd: list[str] = [
        "ffmpeg", "-i", input_file,
        "-map", "0:v", "-map", "0:a?",
        "-sn", "-dn",
        "-map_metadata", "-1", "-map_chapters", "-1",
        "-vf", vf_chain,
        "-bsf:v", "filter_units=remove_types=6",
        "-c:v", "libx264", "-x264-params",
        "sei=0:open-gop=0:no-scenecut=1",
        "-crf", str(crf), "-preset", "slow",
        "-movflags", "+faststart",
    ]

    if audio_mode == "copy":
        cmd += ["-c:a", "copy"]
    elif audio_mode == "aac":
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:   # "none"
        cmd += ["-an"]

    cmd.append(output_file)
    run_ffmpeg_command(cmd)


# ──────────────────────────── merge stub (placeholder) ───────────────────────
def merge_videos_handler(args):
    """
    Placeholder for the 'merge' CLI command.

    The CLI references this symbol to avoid an AttributeError at import time.
    Full implementation will be added later.
    """
    logger.error("merge_videos_handler is not yet implemented.")
    print("❌  The 'merge' command has not been implemented in this build.")
    sys.exit(1)


# ─────────────────────────── CLI entry-point ─────────────────────────────────
def main_entry() -> None:
    if not check_ffmpeg_installed():
        sys.exit("ffmpeg not found in PATH")
    cli.handle_command(cli.parse_arguments())

if __name__ == "__main__":
    main_entry()

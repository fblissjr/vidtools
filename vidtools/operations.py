import sys
import subprocess
import re
import os
import json
import glob
import tempfile
from typing import Sequence
from pathlib import Path
from PIL import Image, ImageOps, ImageFilter
import numpy as np
import datetime

from .utils import run_ffmpeg_command, logger
from . import presets

# --- Core FFmpeg Functions ---

def cut_video(input_file, output_file, start_time=None, end_time=None, duration=None,
              use_copy=True, fast_seek=False, fix_sync=False,
              video_codec=None, audio_codec=None, crf=None):
    """Cut/trim video following ffmpeg best practices."""
    command = ["ffmpeg"]
    if fast_seek and start_time:
        command.extend(["-ss", str(start_time)])
    command.extend(["-i", input_file])
    if not fast_seek and start_time:
        command.extend(["-ss", str(start_time)])
    if end_time:
        command.extend(["-to", str(end_time)])
    elif duration:
        command.extend(["-t", str(duration)])

    if use_copy:
        command.extend(["-c", "copy", "-avoid_negative_ts", "make_zero"])
    else:
        command.extend(["-c:v", video_codec or "libx264"])
        command.extend(["-c:a", audio_codec or "aac"])
        if crf:
            command.extend(["-crf", str(crf)])
    if fix_sync:
        command.extend(["-async", "1"])
    if output_file.lower().endswith('.mp4'):
        command.extend(["-movflags", "+faststart"])
    command.append(output_file)
    run_ffmpeg_command(command)

def resize_video(input_file, output_file, percentage=None, width=None, height=None, algorithm="lanczos"):
    """Resizes a video using ffmpeg scale filter."""
    if percentage:
        scale_filter = f"scale=iw*{percentage}:ih*{percentage}:flags={algorithm}"
    elif width:
        scale_filter = f"scale={width}:-2:flags={algorithm}"
    elif height:
        scale_filter = f"scale=-2:{height}:flags={algorithm}"
    else:
        print("Error: You must specify a percentage, width, or height.")
        return
    command = ["ffmpeg", "-i", input_file, "-vf", scale_filter, "-c:a", "copy"]
    if output_file.lower().endswith('.mp4'):
        command.extend(["-movflags", "+faststart"])
    command.append(output_file)
    run_ffmpeg_command(command)

def convert_format(input_file, output_file, format_type, video_codec=None, audio_codec=None,
                   video_bitrate=None, audio_bitrate=None, quality_scale=None,
                   start_time=None, end_time=None, duration=None, preset=None, use_copy=False):
    """Converts video format using ffmpeg."""
    command = ["ffmpeg"]
    if start_time: command.extend(["-ss", str(start_time)])
    command.extend(["-i", input_file])
    if end_time: command.extend(["-to", str(end_time)])
    if duration: command.extend(["-t", str(duration)])

    if use_copy:
        command.extend(["-c", "copy"])
    elif format_type == "gif":
        command.extend(["-vf", "fps=10,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"])
    else:
        command.extend(["-c:v", video_codec or "libx264"])
        command.extend(["-c:a", audio_codec or "aac"])
        if video_bitrate: command.extend(["-b:v", video_bitrate])
        if audio_bitrate: command.extend(["-b:a", audio_bitrate])
        if quality_scale: command.extend(["-crf", str(quality_scale)])
        else: command.extend(["-crf", "23"])
    if preset: command.extend(["-preset", preset])
    if output_file.lower().endswith('.mp4'):
        command.extend(["-movflags", "+faststart"])
    command.append(output_file)
    run_ffmpeg_command(command)

def extract_audio(input_file, output_file, audio_format="copy", start_time=None, end_time=None, duration=None):
    """Extracts audio from video."""
    command = ["ffmpeg", "-i", input_file, "-vn"]
    if start_time: command.extend(["-ss", str(start_time)])
    if end_time: command.extend(["-to", str(end_time)])
    if duration: command.extend(["-t", str(duration)])
    command.extend(["-c:a", audio_format])
    command.append(output_file)
    run_ffmpeg_command(command)

def extract_frames(input_file, output_pattern, frame_rate=1, image_format="image2", start_time=None, end_time=None, duration=None):
    """Extracts frames from video."""
    command = ["ffmpeg", "-i", input_file]
    if start_time: command.extend(["-ss", str(start_time)])
    if end_time: command.extend(["-to", str(end_time)])
    if duration: command.extend(["-t", str(duration)])
    command.extend(["-r", str(frame_rate), "-f", image_format, output_pattern])
    run_ffmpeg_command(command)

def concatenate_videos(input_files, output_file, use_copy=True):
    """Concatenates video files."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        list_file_path = f.name
        for file in input_files:
            abs_path = os.path.abspath(file)
            f.write(f"file '{abs_path.replace("'", "'\\''")}'\n")
    try:
        command = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file_path]
        command.extend(["-c", "copy" if use_copy else "libx264"])
        if output_file.lower().endswith('.mp4'):
            command.extend(["-movflags", "+faststart"])
        command.append(output_file)
        run_ffmpeg_command(command)
    finally:
        os.remove(list_file_path)

def crop_video(input_file, output_file, width, height, x, y):
    """Crops a video."""
    command = ["ffmpeg", "-i", input_file, "-vf", f"crop={width}:{height}:{x}:{y}", output_file]
    run_ffmpeg_command(command)

def add_subtitles(input_file, output_file, subtitles_file):
    """Adds subtitles to video."""
    command = ["ffmpeg", "-i", input_file, "-vf", f"subtitles={subtitles_file}", output_file]
    run_ffmpeg_command(command)

def rotate_video(input_file, output_file, rotation):
    """Rotates video."""
    transpose = {"90": "1", "180": "2,transpose=2", "270": "2", "-90": "2"}.get(rotation)
    command = ["ffmpeg", "-i", input_file, "-vf", f"transpose={transpose}", output_file]
    run_ffmpeg_command(command)

def get_video_info(input_file):
    """Gets video information using ffprobe."""
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", input_file]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    print(json.dumps(json.loads(result.stdout), indent=4))

def apply_preset(input_file, output_file, preset_name):
    """Applies a preset configuration."""
    all_presets = presets.get_presets()
    if preset_name not in all_presets:
        print(f"Error: Preset '{preset_name}' not found.")
        return
    preset = all_presets[preset_name]
    if "resize_percentage" in preset:
        resize_video(input_file, output_file, percentage=preset["resize_percentage"])
    else:
        convert_format(input_file, output_file, **preset)

def _probe_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) of a media file."""
    with Image.open(path) as img:
        return img.size

def sanitize_video(*, input_file: str, output_file: str, limit: int = 24, noise: int = 6, crf: int = 22,
                   extra_bottom: int = 0, manual_crop: str | None = None, audio_mode: str = "copy"):
    """Auto-crop, add noise, and clean metadata from a video."""
    if manual_crop:
        crop_expr = manual_crop
    else:
        detect_cmd = ["ffmpeg", "-hide_banner", "-i", input_file, "-vf", f"cropdetect=limit={limit}:round=2", "-frames:v", "300", "-f", "null", "-"]
        detect = subprocess.run(detect_cmd, stderr=subprocess.PIPE, text=True)
        matches = re.findall(r"crop=([0-9:]+)", detect.stderr)
        if not matches:
            crop_expr = "iw:ih:0:0"
        else:
            _, h_str, _, _ = matches[-1].split(":")
            new_h = max(2, (int(h_str) - extra_bottom)) & ~1
            crop_expr = f"iw:{new_h}:0:0"

    vf_chain = f"crop={crop_expr},noise=alls={noise}:allf=t+u"
    cmd = ["ffmpeg", "-i", input_file, "-vf", vf_chain, "-map", "0:v", "-map", "0:a?", "-c:v", "libx264",
           "-crf", str(crf), "-preset", "slow", "-movflags", "+faststart", "-map_metadata", "-1"]
    if audio_mode == "copy": cmd.extend(["-c:a", "copy"])
    elif audio_mode == "aac": cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    else: cmd.append("-an")
    cmd.append(output_file)
    run_ffmpeg_command(cmd)

def _parse_crop_value(value_str: str, dimension: int) -> int:
    """Parses a crop value, handling percentages."""
    value_str = value_str.strip()
    if value_str.endswith('%'):
        percentage = float(value_str[:-1])
        return int((percentage / 100) * dimension)
    return int(value_str)

def create_slideshow(image_pattern: str, output_file: str, duration_per_image: float = 3.0, fps: int = 24,
                     size: str | None = None, audio_file: str | None = None,
                     resize_mode: str = "stretch", pad_color: str = "0,0,0", crop_position: str = "center",
                     sanitize_crop: str | None = None, sanitize_noise: int | None = None):
    """Creates a slideshow video from a sequence of images using the PIL + FFmpeg pipeline."""
    image_files = sorted(glob.glob(image_pattern))
    if not image_files:
        print(f"Error: No images found matching pattern: {image_pattern}")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as temp_dir:
        target_w, target_h = (map(int, size.split('x')) if size else _probe_dimensions(image_files[0]))

        print("Preprocessing images with Pillow...")
        for i, img_path in enumerate(image_files):
            try:
                img = Image.open(img_path).convert("RGB")

                # --- Stage 1: Sanitization with PIL/Numpy ---
                if sanitize_crop:
                    parts = sanitize_crop.split(':')
                    if len(parts) != 4:
                        raise ValueError("sanitize_crop format must be 'W:H:X:Y'")

                    # Intelligently parse pixel or percentage values
                    crop_w = _parse_crop_value(parts[0], img.width)
                    crop_h = _parse_crop_value(parts[1], img.height)
                    crop_x = _parse_crop_value(parts[2], img.width)
                    crop_y = _parse_crop_value(parts[3], img.height)

                    if crop_w > img.width or crop_h > img.height or \
                       (crop_x + crop_w) > img.width or (crop_y + crop_h) > img.height:
                        print(f"Error: sanitize-crop dimensions are out of bounds for image {Path(img_path).name}.")
                        sys.exit(1)
                    img = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))

                if sanitize_noise:
                    np_img = np.array(img).astype(np.float32)
                    noise_array = np.random.randn(*np_img.shape) * sanitize_noise
                    np_img = np.clip(np_img + noise_array, 0, 255).astype(np.uint8)
                    img = Image.fromarray(np_img)

                # --- Stage 2: Smart Resizing with PIL ---
                if resize_mode == "stretch":
                    processed_img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                elif resize_mode == "crop":
                    processed_img = ImageOps.fit(img, (target_w, target_h), Image.Resampling.LANCZOS)
                elif resize_mode == "pad":
                    img.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
                    bg_color_tuple = tuple(int(c.strip()) for c in pad_color.split(','))
                    processed_img = Image.new("RGB", (target_w, target_h), bg_color_tuple)
                    paste_x = (target_w - img.width) // 2
                    paste_y = (target_h - img.height) // 2
                    processed_img.paste(img, (paste_x, paste_y))

                temp_output_path = Path(temp_dir) / f"proc_{i:04d}.png"
                processed_img.save(temp_output_path)

            except Exception as e:
                print(f"Error processing image {Path(img_path).name}: {e}")
                sys.exit(1)

        # --- Stage 3: Final Video Encoding with FFmpeg ---
        list_file_path = os.path.join(temp_dir, "filelist.txt")
        with open(list_file_path, "w") as f:
            for i in range(len(image_files)):
                f.write(f"file '{os.path.abspath(Path(temp_dir) / f'proc_{i:04d}.png')}'\n")
                f.write(f"duration {duration_per_image}\n")
            f.write(f"file '{os.path.abspath(Path(temp_dir) / f'proc_{len(image_files)-1:04d}.png')}'\n")

        command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file_path]
        if audio_file:
            command.extend(["-i", audio_file])
        else:
            command.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])

        command.extend(["-c:v", "libx264", "-r", str(fps), "-pix_fmt", "yuv420p"])
        command.extend(["-c:a", "aac", "-shortest"])
        if output_file.lower().endswith('.mp4'):
            command.extend(["-movflags", "+faststart"])
        command.append(output_file)

        print("Creating slideshow video...")
        run_ffmpeg_command(command)

def create_srt_from_transcript(input_file: str, output_file: str):
    """
    Converts a text file with [start --> end] timestamps into a .srt subtitle file.
    """
    logger.info("Starting transcript to SRT conversion", input=input_file, output=output_file)

    def _format_time(seconds_float: float) -> str:
        """Converts a float number of seconds to HH:MM:SS,mmm format."""
        delta = datetime.timedelta(seconds=seconds_float)
        hours, remainder = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        milliseconds = delta.microseconds // 1000
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

    try:
        with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
            pattern = re.compile(r'\[(\d+\.?\d*)\s*-->\s*(\d+\.?\d*)\]\s*(.*)')
            subtitle_index = 1
            for line in infile:
                match = pattern.match(line.strip())
                if match:
                    start_seconds = float(match.group(1))
                    end_seconds = float(match.group(2))
                    text = match.group(3).strip()

                    start_srt = _format_time(start_seconds)
                    end_srt = _format_time(end_seconds)

                    outfile.write(f"{subtitle_index}\n")
                    outfile.write(f"{start_srt} --> {end_srt}\n")
                    outfile.write(f"{text}\n\n")

                    subtitle_index += 1

        logger.info(f"Successfully converted {subtitle_index - 1} subtitle entries.", file=output_file)
        print(f"✅ Successfully created SRT file: {output_file}")

    except FileNotFoundError:
        logger.error("Transcript file not found", file=input_file)
        print(f"Error: The input file '{input_file}' was not found.")
        sys.exit(1)
    except Exception as e:
        logger.error("An error occurred during SRT conversion", error=str(e), exc_info=True)
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

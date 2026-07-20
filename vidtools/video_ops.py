import functools
import platform
import subprocess
import re
import os
import orjson
import glob
import tempfile
from pathlib import Path
from PIL import Image, ImageOps
import numpy as np

from .utils import run_ffmpeg, logger, VIDEO_EXTS, AUDIO_EXTS
from .progress import FfmpegError
from .image_ops import convert_image
from .audio_ops import AUDIO_CODEC_MAP, _auto_codec
from .audio_ops import _add_metadata_policy as _add_audio_metadata_policy
from . import presets

# --- Codec defaults ---

DEFAULT_VIDEO_CODEC = "libx264"
DEFAULT_AUDIO_CODEC = "aac"


def _get_video_encoder(codec: str = DEFAULT_VIDEO_CODEC, hwaccel: bool = False) -> str:
    """Return the appropriate video encoder, using hardware acceleration if requested."""
    if hwaccel and platform.system() == "Darwin":
        hw_map = {"libx264": "h264_videotoolbox", "libx265": "hevc_videotoolbox"}
        return hw_map.get(codec, codec)
    return codec


# --- Helpers ---

_TRANSPOSE_MAP = {"90": "1", "180": "2,transpose=2", "270": "2", "-90": "2"}


def _add_faststart_if_mp4(command: list, output_file: str):
    if output_file.lower().endswith('.mp4'):
        command.extend(["-movflags", "+faststart"])


def _add_time_range_args(command: list, start_time=None, end_time=None, duration=None):
    if start_time:
        command.extend(["-ss", str(start_time)])
    if end_time:
        command.extend(["-to", str(end_time)])
    elif duration:
        command.extend(["-t", str(duration)])


def _build_scale_filter(percentage=None, width=None, height=None, algorithm="lanczos") -> str:
    """Build the ffmpeg `scale=...` filter expression."""
    if percentage:
        return f"scale=iw*{percentage}:ih*{percentage}:flags={algorithm}"
    if width:
        return f"scale={width}:-2:flags={algorithm}"
    if height:
        return f"scale=-2:{height}:flags={algorithm}"
    raise ValueError("Must specify percentage, width, or height")


def _build_transpose_filter(rotation) -> str:
    """Build the ffmpeg `transpose=...` filter expression."""
    code = _TRANSPOSE_MAP.get(str(rotation))
    if code is None:
        raise ValueError(f"Invalid rotation: {rotation}. Use 90, 180, 270, or -90.")
    return f"transpose={code}"


def _escape_subtitle_path(path: str) -> str:
    """Escape filesystem special characters for the ffmpeg subtitles filter."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _apply_encode_options(command: list, video_codec: str | None, audio_codec: str | None,
                          crf: int | None, hwaccel: bool):
    """Append video/audio encoder + quality flags. Handles hwaccel and crf=0 (lossless)."""
    encoder = _get_video_encoder(video_codec or DEFAULT_VIDEO_CODEC, hwaccel)
    command.extend(["-c:v", encoder, "-c:a", audio_codec or DEFAULT_AUDIO_CODEC])
    if hwaccel:
        command.extend(["-q:v", str(crf if crf is not None else 50)])
    elif crf is not None:
        command.extend(["-crf", str(crf)])


# --- Core FFmpeg Functions ---


def _add_metadata_policy(command: list, preserve_metadata: bool = False) -> None:
    """Drop container metadata unless the caller asks to keep it.

    Video ops previously carried every container tag straight through, so
    `trim`, `convert` and `resize` all shipped the source's GPS coordinates,
    camera make/model and capture time into the output. Only `sanitize` stripped
    them. That is the opposite failure from the image path, which historically
    dropped everything -- including data it should have kept.

    **Rotation is unaffected either way**, which is what made this safe to turn
    on by default. Verified against a video carrying a 90-degree display matrix:
    with `-c copy` the matrix survives `-map_metadata -1` intact; on a re-encode
    ffmpeg auto-rotates during decode, so the pixels come out upright and the
    matrix is no longer needed. All three paths display identically.

    Granularity is coarser than the image policy: ffmpeg's `-map_metadata` is
    all-or-nothing at the container level, so provenance tags (title, copyright)
    cannot be kept while dropping location without a separate probe-and-replay
    pass. Defaulting to strip fails closed, which is the right direction when
    the payload can be a home address -- or, as seen on locally generated
    files, a 109 KB comment tag containing an entire generation prompt.
    """
    if not preserve_metadata:
        # Both levels. In testing `-map_metadata -1` alone already cleared the
        # per-stream tags on a stream copy, but the documented scope of the bare
        # form is global metadata only, and `sanitize_video` has always passed
        # both. On a privacy default, match the stricter form rather than rely
        # on behaviour that is not promised.
        command.extend(["-map_metadata", "-1", "-map_metadata:s", "-1"])


def cut_video(input_file, output_file, start_time=None, end_time=None, duration=None,
              use_copy=True, fast_seek=False, fix_sync=False,
              video_codec=None, audio_codec=None, crf=None, hwaccel=False,
              preserve_metadata=False):
    """Cut/trim video following ffmpeg best practices."""
    command = ["ffmpeg", "-y"]
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
        _apply_encode_options(command, video_codec, audio_codec, crf, hwaccel)
    if fix_sync:
        command.extend(["-af", "aresample=async=1"])
    _add_metadata_policy(command, preserve_metadata)
    _add_faststart_if_mp4(command, output_file)
    command.append(output_file)
    run_ffmpeg(command)


def resize_video(input_file, output_file, percentage=None, width=None, height=None,
                 algorithm="lanczos", preserve_metadata=False):
    """Resizes a video using ffmpeg scale filter."""
    scale_filter = _build_scale_filter(percentage, width, height, algorithm)
    command = ["ffmpeg", "-y", "-i", input_file, "-vf", scale_filter, "-c:a", "copy"]
    _add_metadata_policy(command, preserve_metadata)
    _add_faststart_if_mp4(command, output_file)
    command.append(output_file)
    run_ffmpeg(command)


def convert_format(input_file, output_file, format_type, video_codec=None, audio_codec=None,
                   video_bitrate=None, audio_bitrate=None, quality_scale=None,
                   start_time=None, end_time=None, duration=None, preset=None, use_copy=False,
                   hwaccel=False, preserve_metadata=False):
    """Converts video format using ffmpeg."""
    command = ["ffmpeg", "-y"]
    if start_time: command.extend(["-ss", str(start_time)])
    command.extend(["-i", input_file])
    if end_time: command.extend(["-to", str(end_time)])
    if duration: command.extend(["-t", str(duration)])

    out_ext = os.path.splitext(output_file)[1].lstrip(".").lower()
    audio_only = out_ext in AUDIO_CODEC_MAP

    if audio_only:
        # The output container holds no video stream (e.g. mp4 -> mp3). Drop
        # video and pick the codec the container actually accepts; emitting
        # `-c:v libx264 -c:a aac` into an .mp3 muxer fails ("Nothing was written").
        command.append("-vn")
        if use_copy:
            command.extend(["-c:a", "copy"])
        else:
            command.extend(["-c:a", _auto_codec(output_file, audio_codec)])
            if audio_bitrate: command.extend(["-b:a", audio_bitrate])
    elif use_copy:
        command.extend(["-c", "copy"])
    elif format_type == "gif":
        command.extend(["-vf", "fps=10,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"])
    else:
        encoder = _get_video_encoder(video_codec or DEFAULT_VIDEO_CODEC, hwaccel)
        command.extend(["-c:v", encoder])
        command.extend(["-c:a", audio_codec or DEFAULT_AUDIO_CODEC])
        if video_bitrate: command.extend(["-b:v", video_bitrate])
        if audio_bitrate: command.extend(["-b:a", audio_bitrate])
        if hwaccel:
            command.extend(["-q:v", str(quality_scale or 50)])
        elif quality_scale:
            command.extend(["-crf", str(quality_scale)])
        else:
            command.extend(["-crf", "23"])
    if preset: command.extend(["-preset", preset])
    _add_metadata_policy(command, preserve_metadata)
    _add_faststart_if_mp4(command, output_file)
    command.append(output_file)
    run_ffmpeg(command)


def extract_audio(input_file, output_file, audio_format="copy", start_time=None, end_time=None,
                  duration=None, preserve_metadata: bool = False):
    """Extracts audio from video.

    Takes the *audio* policy, not the video one: the output is an audio file, so
    descriptive tags (title, artist, album) survive while free-form and recorder
    fields (comment, location, make, model) are blanked. Ripping the audio off a
    video used to carry the container's GPS and comment through untouched.
    """
    command = ["ffmpeg", "-y", "-i", input_file, "-vn"]
    _add_time_range_args(command, start_time, end_time, duration)
    command.extend(["-c:a", audio_format])
    _add_audio_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def extract_frames(input_file, output_pattern, frame_rate=1, image_format="image2",
                   start_time=None, end_time=None, duration=None,
                   preserve_metadata: bool = False):
    """Extracts frames from video.

    Frames are images, so the strip-by-default policy applies. Most still
    encoders ignore container tags anyway, but the muxer is chosen by the caller
    via `image_format` and some (e.g. writing to a TIFF or a video container)
    do carry them, so the policy is applied rather than assumed unnecessary.
    """
    if frame_rate <= 0:
        raise ValueError(f"frame_rate must be positive, got {frame_rate}")
    command = ["ffmpeg", "-y", "-i", input_file]
    _add_time_range_args(command, start_time, end_time, duration)
    command.extend(["-r", str(frame_rate), "-f", image_format])
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_pattern)
    run_ffmpeg(command)


def _grab_frame(input_file, output_file, timestamp, accurate):
    """Decode exactly one frame to `output_file`. Fast (keyframe) seek by default
    via input `-ss`; `accurate=True` uses output `-ss` to decode to the precise
    frame (slower on long inputs). The image2 muxer picks the encoder from the
    output extension.

    Seeking past the end of the input makes ffmpeg write zero frames and still
    exit 0, so we verify a non-empty frame was produced and raise otherwise."""
    has_seek = timestamp is not None and str(timestamp) != ""
    command = ["ffmpeg", "-y"]
    if has_seek and not accurate:
        command.extend(["-ss", str(timestamp)])
    command.extend(["-i", input_file])
    if has_seek and accurate:
        command.extend(["-ss", str(timestamp)])
    command.extend(["-frames:v", "1", "-update", "1", output_file])
    run_ffmpeg(command)
    if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
        raise FfmpegError(
            f"No frame was produced at timestamp {timestamp!r}; "
            "the timestamp may be past the end of the video."
        )


def screenshot(input_file, output_file, timestamp=None, quality=95, accurate=False):
    """Grab a single still frame from a video at `timestamp` (e.g. "00:01:30",
    "90", or seconds). Output format is inferred from `output_file`'s extension.

    PNG is lossless and ignores `quality` (recommended for max fidelity). For
    lossy formats (jpg/webp) ffmpeg first extracts a lossless PNG, then PIL
    re-encodes with `quality` (1-100, higher = better) -- this keeps one
    consistent quality scale and works even when ffmpeg lacks libwebp/mjpeg
    quality control. `accurate=True` decodes to the exact frame (slower)."""
    ext = os.path.splitext(output_file)[1].lower()
    if ext in (".jpg", ".jpeg", ".webp"):
        fd, tmp_png = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            _grab_frame(input_file, tmp_png, timestamp, accurate)
            convert_image(tmp_png, output_file, quality=quality)
        finally:
            if os.path.exists(tmp_png):
                os.remove(tmp_png)
    else:
        _grab_frame(input_file, output_file, timestamp, accurate)
    logger.info("Screenshot captured", output=output_file, timestamp=timestamp)


def concatenate_videos(input_files, output_file, use_copy=True, preserve_metadata=False):
    """Concatenates video files."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        list_file_path = f.name
        for file in input_files:
            abs_path = os.path.abspath(file)
            f.write(f"file '{abs_path.replace("'", "'\\''")}'\n")
    try:
        command = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file_path]
        if use_copy:
            command.extend(["-c", "copy"])
        else:
            command.extend(["-c:v", "libx264", "-c:a", "aac"])
        _add_metadata_policy(command, preserve_metadata)
        _add_faststart_if_mp4(command, output_file)
        command.append(output_file)
        run_ffmpeg(command)
    finally:
        os.remove(list_file_path)


def crop_video(input_file, output_file, width, height, x, y, preserve_metadata=False):
    """Crops a video. ffmpeg fails late and cryptically on bad geometry, so
    reject negative/zero sizes and negative offsets up front. (Full bounds
    checking would need an ffprobe pass; crop_image can check cheaply because
    PIL already has the dimensions loaded.)"""
    if width <= 0 or height <= 0:
        raise ValueError(f"Crop width/height must be positive, got {width}x{height}")
    if x < 0 or y < 0:
        raise ValueError(f"Crop offset must be non-negative, got x={x} y={y}")
    command = ["ffmpeg", "-y", "-i", input_file, "-vf", f"crop={width}:{height}:{x}:{y}"]
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


@functools.lru_cache(maxsize=None)
def _ffmpeg_has_filter(name: str) -> bool:
    """Whether this ffmpeg build exposes `name`. Cached: it cannot change
    mid-process and `-filters` lists hundreds of entries."""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def add_subtitles(input_file, output_file, subtitles_file, preserve_metadata=False):
    """Burn subtitles into the video.

    Requires an ffmpeg built with libass. Homebrew's default formula ships
    without it, and the failure is otherwise a filter-parse error that names no
    cause -- the same shape as the libwebp gap that hid in `screenshot`.
    """
    if not _ffmpeg_has_filter("subtitles"):
        raise FfmpegError(
            "This ffmpeg has no 'subtitles' filter, so subtitles cannot be "
            "burned in. It needs to be built with libass "
            "(e.g. 'brew install ffmpeg --with-libass', or a full build)."
        )
    escaped = _escape_subtitle_path(subtitles_file)
    command = ["ffmpeg", "-y", "-i", input_file, "-vf", f"subtitles='{escaped}'"]
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def rotate_video(input_file, output_file, rotation, preserve_metadata=False):
    """Rotates video."""
    command = ["ffmpeg", "-y", "-i", input_file, "-vf",
               _build_transpose_filter(rotation)]
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def get_video_info(input_file):
    """Gets video information using ffprobe."""
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", input_file]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return orjson.loads(result.stdout)


_PRESET_KEY_MAP = {
    "format": "format_type", "vcodec": "video_codec", "acodec": "audio_codec",
    "quality": "quality_scale", "vbitrate": "video_bitrate", "abitrate": "audio_bitrate",
}
_PRESET_IGNORE = {"description", "resize_percentage"}


def apply_preset(input_file, output_file, preset_name):
    """Applies a preset configuration."""
    all_presets = presets.get_presets()
    if preset_name not in all_presets:
        raise ValueError(f"Preset '{preset_name}' not found.")
    preset = all_presets[preset_name]
    if "resize_percentage" in preset:
        resize_video(input_file, output_file, percentage=preset["resize_percentage"])
    else:
        kwargs = {}
        for k, v in preset.items():
            if k in _PRESET_IGNORE or v is None:
                continue
            kwargs[_PRESET_KEY_MAP.get(k, k)] = v
        convert_format(input_file, output_file, **kwargs)


def _probe_dimensions(path: str) -> tuple[int, int]:
    """Return (width, height) of a media file."""
    with Image.open(path) as img:
        return img.size


def sanitize_video(*, input_file: str, output_file: str, limit: int = 24, noise: int = 6, crf: int = 22,
                   extra_bottom: int = 0, manual_crop: str | None = None, audio_mode: str = "aac"):
    """Auto-crop letterbox, add noise, re-encode, and strip identifying metadata.

    What this defeats:
      - Container and stream metadata fingerprints (-map_metadata + -map_metadata:s)
      - ffmpeg encoder version leak in output (-fflags +bitexact)
      - Pixel-level perceptual hashes (noise filter + libx264 re-encode)
      - Audio fingerprint matching (default audio_mode='aac' re-encodes; pass
        audio_mode='copy' to preserve the original audio bytes)

    Cropdetect uses the ffmpeg-returned W:H:X:Y so top letterbox is also
    removed, not just bottom. extra_bottom shaves additional pixels from the
    detected height.
    """
    if manual_crop:
        crop_expr = manual_crop
    else:
        detect_cmd = ["ffmpeg", "-hide_banner", "-i", input_file, "-vf", f"cropdetect=limit={limit}:round=2", "-frames:v", "300", "-f", "null", "-"]
        detect = subprocess.run(detect_cmd, stderr=subprocess.PIPE, text=True)
        matches = re.findall(r"crop=([0-9]+:[0-9]+:[0-9]+:[0-9]+)", detect.stderr)
        if not matches:
            crop_expr = "iw:ih:0:0"
        else:
            w_str, h_str, x_str, y_str = matches[-1].split(":")
            new_h = max(2, (int(h_str) - extra_bottom)) & ~1
            crop_expr = f"{w_str}:{new_h}:{x_str}:{y_str}"

    vf_chain = f"crop={crop_expr},noise=alls={noise}:allf=t+u"
    cmd = ["ffmpeg", "-y", "-i", input_file,
           "-vf", vf_chain, "-map", "0:v", "-map", "0:a?",
           "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
           "-flags:v", "+bitexact",
           "-movflags", "+faststart",
           "-fflags", "+bitexact",
           # Strip container metadata, then per-stream metadata, then explicitly
           # clear the encoder tag the libx264/aac encoders re-add post-encode.
           "-map_metadata", "-1", "-map_metadata:s", "-1",
           "-metadata:s:v:0", "encoder="]
    if audio_mode == "copy":
        cmd.extend(["-c:a", "copy"])
    elif audio_mode == "aac":
        cmd.extend(["-c:a", "aac", "-b:a", "128k", "-flags:a", "+bitexact",
                    "-metadata:s:a:0", "encoder="])
    else:
        cmd.append("-an")
    cmd.append(output_file)
    run_ffmpeg(cmd)


def _stream_duration(stream: dict, format_info: dict) -> float | None:
    """Resolve a stream's duration in seconds, falling back to format.duration.
    Returns None if neither is available or parseable."""
    for source in (stream.get("duration"), format_info.get("duration")):
        if source in (None, "N/A", ""):
            continue
        try:
            return float(source)
        except (TypeError, ValueError):
            continue
    return None


def trim_to_audio(input_file: str, output_file: str, buffer: float = 0.25,
                  preserve_metadata: bool = False,
                  accurate: bool = False, video_codec: str | None = None,
                  audio_codec: str | None = None, crf: int | None = None,
                  hwaccel: bool = False):
    """Trim video so its duration matches the audio track plus a small buffer.

    For videos whose video track outlasts the audio (e.g. avatar/TTS-generated
    clips with trailing silence). Probes each stream's duration via ffprobe and
    applies `-t (audio_duration + buffer)` to the output.

    Stream-copies by default (fast, lossless, keyframe-imprecise). Pass
    `accurate=True` to re-encode for frame-accurate trimming. The buffer
    avoids an abrupt cut at the exact audio end; default 0.25s.

    If the audio is already longer than the video (or `audio + buffer >= video`),
    the file is remuxed without trimming.
    """
    if buffer < 0:
        raise ValueError(f"buffer must be >= 0, got {buffer}")

    info = get_video_info(input_file)
    streams = info.get("streams", [])
    format_info = info.get("format", {})

    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if audio is None:
        raise ValueError(f"Input has no audio stream: {input_file}")
    if video is None:
        raise ValueError(f"Input has no video stream: {input_file}")

    audio_dur = _stream_duration(audio, format_info)
    video_dur = _stream_duration(video, format_info)

    command = ["ffmpeg", "-y", "-i", input_file]

    if audio_dur is not None and video_dur is not None:
        target = audio_dur + buffer
        if target < video_dur:
            _add_time_range_args(command, duration=f"{target:.3f}")

    if accurate:
        _apply_encode_options(command, video_codec, audio_codec, crf, hwaccel)
    else:
        command.extend(["-c", "copy", "-avoid_negative_ts", "make_zero"])

    _add_metadata_policy(command, preserve_metadata)
    _add_faststart_if_mp4(command, output_file)
    command.append(output_file)
    run_ffmpeg(command)


def _parse_crop_value(value_str: str, dimension: int) -> int:
    """Parses a crop value, handling percentages."""
    value_str = value_str.strip()
    if value_str.endswith('%'):
        percentage = float(value_str[:-1])
        return int((percentage / 100) * dimension)
    return int(value_str)


def create_slideshow(image_pattern: str, output_file: str, duration_per_image: float = 3.0, fps: int = 24,
                     size: str | None = None, audio_file: str | None = None,
                     resize_mode: str = "stretch", pad_color: str = "0,0,0",
                     sanitize_crop: str | None = None, sanitize_noise: int | None = None,
                     preserve_metadata: bool = False):
    """Creates a slideshow video from a sequence of images using the PIL + FFmpeg pipeline."""
    image_files = sorted(glob.glob(image_pattern))
    if not image_files:
        raise ValueError(f"No images found matching pattern: {image_pattern}")

    with tempfile.TemporaryDirectory() as temp_dir:
        target_w, target_h = (map(int, size.split('x')) if size else _probe_dimensions(image_files[0]))

        logger.info("Preprocessing images with Pillow")
        for i, img_path in enumerate(image_files):
            img = Image.open(img_path).convert("RGB")

            # --- Stage 1: Sanitization with PIL/Numpy ---
            if sanitize_crop:
                parts = sanitize_crop.split(':')
                if len(parts) != 4:
                    raise ValueError("sanitize_crop format must be 'W:H:X:Y'")

                crop_w = _parse_crop_value(parts[0], img.width)
                crop_h = _parse_crop_value(parts[1], img.height)
                crop_x = _parse_crop_value(parts[2], img.width)
                crop_y = _parse_crop_value(parts[3], img.height)

                if crop_w > img.width or crop_h > img.height or \
                   (crop_x + crop_w) > img.width or (crop_y + crop_h) > img.height:
                    raise ValueError(f"sanitize-crop dimensions out of bounds for image {Path(img_path).name}")
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
            else:
                raise ValueError(f"Invalid resize_mode: '{resize_mode}'. Use 'stretch', 'crop', or 'pad'.")

            temp_output_path = Path(temp_dir) / f"proc_{i:04d}.png"
            processed_img.save(temp_output_path)

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
        _add_metadata_policy(command, preserve_metadata)
        _add_faststart_if_mp4(command, output_file)
        command.append(output_file)

        logger.info("Creating slideshow video")
        run_ffmpeg(command)


def add_audio(input_file: str, audio_file: str, output_file: str,
              mode: str = "replace", volume: float = 1.0,
              preserve_metadata: bool = False):
    """Add an audio track to a video file.

    Args:
        input_file: Path to the video file.
        audio_file: Path to the audio file to add.
        output_file: Path for the output file.
        mode: "replace" to replace existing audio (default),
              "mix" to layer new audio on top of existing.
        volume: Volume multiplier for the added audio (0.0 to 10.0, default 1.0).
    """
    if mode not in ("replace", "mix"):
        raise ValueError(f"Invalid mode: '{mode}'. Use 'replace' or 'mix'.")
    if volume < 0.0 or volume > 10.0:
        raise ValueError(f"volume must be between 0.0 and 10.0, got {volume}")

    command = ["ffmpeg", "-y", "-i", input_file, "-i", audio_file]

    if mode == "replace":
        command.extend(["-map", "0:v", "-map", "1:a", "-c:v", "copy"])
        if volume != 1.0:
            command.extend(["-af", f"volume={volume}", "-c:a", "aac"])
        else:
            command.extend(["-c:a", "copy"])
        command.append("-shortest")
    else:
        # mix mode: amix handles duration via duration=shortest
        if volume != 1.0:
            filter_expr = f"[1:a]volume={volume}[adj];[0:a][adj]amix=inputs=2:duration=shortest"
        else:
            filter_expr = "[0:a][1:a]amix=inputs=2:duration=shortest"
        command.extend(["-filter_complex", filter_expr, "-map", "0:v", "-c:v", "copy"])
    _add_metadata_policy(command, preserve_metadata)
    _add_faststart_if_mp4(command, output_file)
    command.append(output_file)
    run_ffmpeg(command)


def apply_audio_op_keep_video(input_file: str, output_file: str, audio_op):
    """Run an audio operation, keeping the video when the input is one.

    `audio_op(in_path, out_path)` processes audio. Routing by file type:
      - non-video input, or audio output container: run `audio_op` directly.
      - video input + video output: process the audio into a temp .m4a (AAC --
        a container-friendly codec; a raw-PCM .wav would mux into mp4 as a
        non-standard stream many players reject) and remux it over the original
        video, copying the video stream.

    Shared by the CLI audio verbs (normalize/fade/resample/clean) and the web
    `clean` dispatch so both behave identically on video inputs."""
    in_ext = os.path.splitext(input_file)[1].lower()
    out_ext = os.path.splitext(output_file)[1].lower()
    if in_ext not in VIDEO_EXTS or out_ext in AUDIO_EXTS:
        audio_op(input_file, output_file)
        return
    fd, tmp_audio = tempfile.mkstemp(suffix=".m4a")
    os.close(fd)
    try:
        audio_op(input_file, tmp_audio)
        add_audio(input_file, tmp_audio, output_file, mode="replace")
    finally:
        if os.path.exists(tmp_audio):
            os.remove(tmp_audio)


def create_srt_from_transcript(input_file: str, output_file: str):
    """
    Converts a text file with [start --> end] timestamps into a .srt subtitle file.
    """
    logger.info("Starting transcript to SRT conversion", input=input_file, output=output_file)

    def _format_time(seconds_float: float) -> str:
        """Converts a float number of seconds to HH:MM:SS,mmm format.

        Computed from total milliseconds rather than `timedelta`: `.seconds`
        caps at 86399 and rolls the remainder into `.days`, so hour 25 rendered
        as hour 1 -- a subtitle file that looks valid and is silently wrong past
        the 24-hour mark. Rounding rather than truncating also stops 1.9995s
        landing on the previous millisecond.
        """
        total_ms = int(round(seconds_float * 1000))
        hours, rem = divmod(total_ms, 3_600_000)
        minutes, rem = divmod(rem, 60_000)
        seconds, milliseconds = divmod(rem, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"

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

    logger.info(f"Converted {subtitle_index - 1} subtitle entries", file=output_file)

"""Audio operations using FFmpeg."""

import re
import subprocess
from pathlib import Path

from .utils import run_ffmpeg, logger
from .progress import report_progress

# Map output extension to ffmpeg audio codec
AUDIO_CODEC_MAP = {
    "mp3": "libmp3lame",
    "aac": "aac",
    "m4a": "aac",
    "flac": "flac",
    "ogg": "libvorbis",
    "opus": "libopus",
    "wav": "pcm_s16le",
}


#: Tags dropped by default. Free-form or technical fields that can carry an
#: arbitrary payload -- the class that showed up as a 109 KB generation prompt in
#: video comment tags -- plus anything locating the recording.
_DROPPED_AUDIO_TAGS = (
    "comment", "description", "encoded_by", "encoder",
    "location", "location-eng", "make", "model",
)


def _add_metadata_policy(command: list, preserve_metadata: bool = False) -> None:
    """Drop free-form and identifying tags, keep the descriptive ones.

    **This deliberately differs from the image and video policy, which strip
    everything by default.** For a photo or a clip, metadata is incidental and
    nobody misses it. For audio it is frequently the point: artist, title, album
    and track are what make a library work, and `convert song.mp3 song.m4a`
    silently returning an untitled file would be a worse bug than the leak.

    So the same three-category model applies with different membership. Kept:
    artist, title, album, track, genre, date, composer, copyright. Dropped:
    `comment` and friends, which hold arbitrary text, plus recorder fields.

    Implemented by keeping the source map and blanking specific keys rather than
    `-map_metadata -1` plus a re-add, which would need a separate ffprobe pass.
    """
    if preserve_metadata:
        return
    command.extend(["-map_metadata", "0"])
    for tag in _DROPPED_AUDIO_TAGS:
        command.extend(["-metadata", f"{tag}="])


def _auto_codec(output_file: str, explicit_codec: str | None = None) -> str:
    """Determine audio codec from output extension or explicit override."""
    if explicit_codec:
        return explicit_codec
    ext = Path(output_file).suffix.lstrip(".").lower()
    return AUDIO_CODEC_MAP.get(ext, "aac")


def convert_audio(input_file: str, output_file: str, audio_codec: str | None = None,
                  preserve_metadata: bool = False,
                  bitrate: str | None = None, sample_rate: int | None = None):
    """Convert audio format with codec auto-detection from output extension."""
    codec = _auto_codec(output_file, audio_codec)
    command = ["ffmpeg", "-y", "-i", input_file, "-vn", "-c:a", codec]
    if bitrate:
        command.extend(["-b:a", bitrate])
    if sample_rate:
        command.extend(["-ar", str(sample_rate)])
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def cut_audio(input_file: str, output_file: str, start_time: str | None = None,
              preserve_metadata: bool = False,
              end_time: str | None = None, duration: str | None = None,
              use_copy: bool = True):
    """Trim an audio file by time range."""
    command = ["ffmpeg", "-y"]
    if start_time:
        command.extend(["-ss", str(start_time)])
    command.extend(["-i", input_file])
    if end_time:
        command.extend(["-to", str(end_time)])
    elif duration:
        command.extend(["-t", str(duration)])
    command.extend(["-vn"])
    if use_copy:
        command.extend(["-c", "copy"])
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def normalize_audio(input_file: str, output_file: str, target_loudness: float = -16.0,
                    preserve_metadata: bool = False):
    """Two-pass loudnorm normalization."""
    # Pass 1: analyze
    report_progress(10, "Analyzing audio levels...")
    analyze_cmd = [
        "ffmpeg", "-y", "-i", input_file, "-vn",
        "-af", f"loudnorm=I={target_loudness}:print_format=summary",
        "-f", "null", "-"
    ]
    result = subprocess.run(analyze_cmd, capture_output=True, text=True)
    stderr = result.stderr

    # Parse measured values
    measured_i = re.search(r'Input Integrated:\s+([-\d.]+)', stderr)
    measured_tp = re.search(r'Input True Peak:\s+([-\d.]+)', stderr)
    measured_lra = re.search(r'Input LRA:\s+([-\d.]+)', stderr)
    measured_thresh = re.search(r'Input Threshold:\s+([-\d.]+)', stderr)

    if measured_i and measured_tp and measured_lra and measured_thresh:
        # Pass 2: apply with measured values for linear normalization
        loudnorm_filter = (
            f"loudnorm=I={target_loudness}:"
            f"measured_I={measured_i.group(1)}:"
            f"measured_TP={measured_tp.group(1)}:"
            f"measured_LRA={measured_lra.group(1)}:"
            f"measured_thresh={measured_thresh.group(1)}:"
            f"linear=true"
        )
    else:
        # Fallback to single-pass
        logger.warning("Could not parse loudnorm analysis, using single-pass normalization")
        loudnorm_filter = f"loudnorm=I={target_loudness}"

    command = ["ffmpeg", "-y", "-i", input_file, "-vn", "-af", loudnorm_filter]
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def mix_audio(input_files: list[str], output_file: str, preserve_metadata: bool = False,
              volumes: list[float] | None = None):
    """Mix multiple audio files together using amix filter."""
    if len(input_files) < 2:
        raise ValueError("mix_audio requires at least 2 input files")

    command = ["ffmpeg", "-y"]
    for f in input_files:
        command.extend(["-i", f])

    n = len(input_files)
    if volumes:
        # Apply volume adjustment per input, then mix
        vol_parts = []
        for i, vol in enumerate(volumes):
            vol_parts.append(f"[{i}:a]volume={vol}[a{i}]")
        mix_inputs = "".join(f"[a{i}]" for i in range(n))
        filter_expr = ";".join(vol_parts) + f";{mix_inputs}amix=inputs={n}:normalize=0"
    else:
        mix_inputs = "".join(f"[{i}:a]" for i in range(n))
        filter_expr = f"{mix_inputs}amix=inputs={n}:normalize=0"

    command.extend(["-filter_complex", filter_expr, "-vn"])
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def get_audio_info(input_file: str) -> dict:
    """Get audio file metadata using ffprobe."""
    import orjson
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams",
               "-select_streams", "a", "-of", "json", input_file]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return orjson.loads(result.stdout)


def fade_audio(input_file: str, output_file: str, preserve_metadata: bool = False,
               fade_in: float | None = None, fade_out: float | None = None,
               total_duration: float | None = None):
    """Apply fade in/out effects to audio."""
    filters = []
    if fade_in is not None:
        filters.append(f"afade=t=in:d={fade_in}")
    if fade_out is not None:
        if total_duration is None:
            raise ValueError("total_duration is required for fade_out")
        start = total_duration - fade_out
        filters.append(f"afade=t=out:st={start}:d={fade_out}")

    if not filters:
        raise ValueError("At least one of fade_in or fade_out must be specified")

    command = ["ffmpeg", "-y", "-i", input_file, "-vn", "-af", ",".join(filters)]
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def resample_audio(input_file: str, output_file: str, sample_rate: int = 44100,
                   preserve_metadata: bool = False):
    """Resample audio to a different sample rate."""
    command = ["ffmpeg", "-y", "-i", input_file, "-vn", "-ar", str(sample_rate)]
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def detect_silence(input_file: str, threshold: int = -30,
                   min_duration: float = 0.5) -> list[tuple[float, float]]:
    """Detect silence ranges in audio. Returns list of (start, end) tuples."""
    command = [
        "ffmpeg", "-i", input_file,
        "-af", f"silencedetect=noise={threshold}dB:d={min_duration}",
        "-f", "null", "-"
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    stderr = result.stderr

    starts = [float(m.group(1)) for m in re.finditer(r'silence_start:\s*([\d.]+)', stderr)]
    ends = [float(m.group(1)) for m in re.finditer(r'silence_end:\s*([\d.]+)', stderr)]

    return list(zip(starts, ends[:len(starts)]))


def clean_audio(input_file: str, output_file: str, *,
                preserve_metadata: bool = False,
                highpass: int | None = 80,
                denoise: float | None = -25.0,
                compress: bool = True,
                compress_threshold: float = -15.0,
                compress_ratio: float = 3.0,
                compress_attack: float = 5.0,
                compress_release: float = 50.0,
                loudnorm: bool = True,
                target_lufs: float = -14.0,
                true_peak: float = -1.0,
                lra: float = 11.0,
                audio_codec: str | None = None,
                bitrate: str | None = None):
    """Clean up a vocal-heavy audio track in a single ffmpeg pass.

    Pipeline (each stage independently skippable):
      1. highpass=f=N         -- removes rumble, plosives, AC hum
      2. afftdn=nf=N          -- FFT-based hiss/broadband noise reduction
      3. acompressor=...      -- dynamic range compression
      4. loudnorm=I:TP:LRA    -- EBU R128 loudness normalization

    Pass None for `highpass` or `denoise`, or False for `compress`/`loudnorm`,
    to skip individual stages. Defaults are tuned for spoken vocal / sung lead.
    """
    filters: list[str] = []
    if highpass:
        filters.append(f"highpass=f={highpass}")
    if denoise is not None:
        filters.append(f"afftdn=nf={denoise:g}")
    if compress:
        filters.append(
            f"acompressor=threshold={compress_threshold:g}dB:ratio={compress_ratio:g}:"
            f"attack={compress_attack:g}:release={compress_release:g}"
        )
    if loudnorm:
        # Don't apply :g here so float defaults like -1.0 keep their .0 form
        # (matches EBU R128 conventional notation).
        filters.append(f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={lra}")

    if not filters:
        raise ValueError("clean_audio requires at least one stage enabled")

    codec = _auto_codec(output_file, audio_codec)
    command = ["ffmpeg", "-y", "-i", input_file, "-vn",
               "-af", ",".join(filters), "-c:a", codec]
    if bitrate:
        command.extend(["-b:a", bitrate])
    elif codec == "libmp3lame":
        # VBR ~190 kbps, matches the recipe; only valid for libmp3lame
        command.extend(["-q:a", "2"])
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)


def remove_silence(input_file: str, output_file: str,
                   threshold: int = -30, min_duration: float = 0.5,
                   preserve_metadata: bool = False):
    """Remove silence from audio using silenceremove filter."""
    command = [
        "ffmpeg", "-y", "-i", input_file, "-vn",
        "-af", f"silenceremove=stop_periods=-1:stop_duration={min_duration}:stop_threshold={threshold}dB",
    ]
    _add_metadata_policy(command, preserve_metadata)
    command.append(output_file)
    run_ffmpeg(command)

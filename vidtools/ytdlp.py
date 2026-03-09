"""yt-dlp wrapper for downloading video/audio content.

Downloads video/audio from URLs via subprocess with progress parsing.
"""

import re
import subprocess
from pathlib import Path
from typing import Callable

import structlog

logger = structlog.get_logger(__name__)

ALLOWED_FORMATS = {"mp3", "wav", "mp4"}
DEFAULT_MAX_DURATION = 7200  # 2 hours

# Regex for yt-dlp download progress lines
_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d+\.?\d*)%\s+of\s+[\d\.]+\w+\s+at\s+([\d\.]+\w+/s)"
)
_PROGRESS_DONE_RE = re.compile(r"\[download\]\s+100%")


def parse_ytdlp_progress(line: str) -> dict | None:
    """Parse a yt-dlp stderr/stdout line for download progress.

    Returns dict with 'percent' and 'speed' keys, or None if not a progress line.
    """
    if not line:
        return None

    match = _PROGRESS_RE.search(line)
    if match:
        return {
            "percent": int(float(match.group(1))),
            "speed": match.group(2),
        }

    if _PROGRESS_DONE_RE.search(line):
        return {"percent": 100, "speed": ""}

    return None


def build_ytdlp_command(
    url: str,
    output_path: str,
    format_type: str,
    max_duration: int | None = DEFAULT_MAX_DURATION,
) -> list[str]:
    """Build a yt-dlp command list (no shell=True).

    Args:
        url: URL to download.
        output_path: Full output file path.
        format_type: One of 'mp3', 'wav', 'mp4'.
        max_duration: Max video duration in seconds. None to disable.
    """
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--print", "after_move:filepath",
    ]

    if format_type in ("mp3", "wav"):
        cmd.extend([
            "--extract-audio",
            "--audio-format", format_type,
            "--audio-quality", "0",
        ])
    else:
        cmd.extend([
            "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
        ])

    if max_duration:
        cmd.extend(["--match-filter", f"duration<={max_duration}"])

    cmd.extend(["-o", output_path])
    cmd.append(url)

    return cmd


def validate_download_params(format_type: str) -> list[str]:
    """Validate download parameters. Returns list of error strings (empty = valid)."""
    errors = []
    if format_type not in ALLOWED_FORMATS:
        errors.append(f"Invalid format '{format_type}'. Allowed: {', '.join(sorted(ALLOWED_FORMATS))}")
    return errors


def download(
    url: str,
    output_path: str,
    format_type: str,
    progress_callback: Callable | None = None,
    max_duration: int | None = DEFAULT_MAX_DURATION,
) -> str:
    """Download video/audio from a URL.

    Args:
        url: URL to download.
        output_path: Output file path (may be modified by yt-dlp for audio extraction).
        format_type: 'mp3', 'wav', or 'mp4'.
        progress_callback: Optional callback(percent: int, message: str).
        max_duration: Max video duration in seconds.

    Returns:
        Path to the downloaded file.

    Raises:
        RuntimeError: If download fails.
    """
    cmd = build_ytdlp_command(url, output_path, format_type, max_duration)
    logger.info("Starting yt-dlp download", url=url, format=format_type)

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True
    )

    actual_output = output_path
    printed_filepath = None

    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if line:
            line = line.strip()
            logger.debug("yt-dlp output", line=line)

            # --print after_move:filepath outputs the final path as a bare line
            # after download completes. It won't start with '['.
            if line and not line.startswith("[") and not line.startswith("WARNING"):
                candidate = Path(line)
                if candidate.exists() and candidate.is_file():
                    printed_filepath = str(candidate)

            progress = parse_ytdlp_progress(line)
            if progress and progress_callback:
                progress_callback(
                    progress["percent"],
                    f"Downloading... {progress['percent']}%"
                    + (f" ({progress['speed']})" if progress.get("speed") else ""),
                )

    if process.returncode != 0:
        raise RuntimeError(f"yt-dlp failed with exit code {process.returncode}")

    # Use the --print filepath if available, otherwise fall back
    if printed_filepath:
        actual_output = printed_filepath
    elif not Path(actual_output).exists():
        # Last resort: scan directory for our file
        stem = Path(output_path).stem
        parent = Path(output_path).parent
        for candidate in parent.iterdir():
            if candidate.is_file() and candidate.stem.startswith(stem.split(".")[0]):
                actual_output = str(candidate)
                break

    if progress_callback:
        progress_callback(100, "Download complete")

    logger.info("yt-dlp download complete", output=actual_output)
    return actual_output

"""yt-dlp wrapper for downloading video/audio content.

Downloads video/audio from URLs via subprocess with progress parsing.
"""

import re
import subprocess
from pathlib import Path

import structlog
from .progress import report_progress

logger = structlog.get_logger(__name__)

ALLOWED_FORMATS = {"mp3", "wav", "mp4"}
DEFAULT_MAX_DURATION = 7200  # 2 hours

# Regex for yt-dlp download progress lines
_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d+\.?\d*)%\s+of\s+[\d\.]+\w+\s+at\s+([\d\.]+\w+/s)"
)
_PROGRESS_DONE_RE = re.compile(r"\[download\]\s+100%")

# Live-broadcast progress emits fragment counts, not percentages.
_LIVE_FRAG_RE = re.compile(r"\[download\] Downloading fragment (\d+)")
_LIVE_HLS_SEG_RE = re.compile(r"\[hls\b.*\bOpening\b.*'([^']+)'")
_LIVE_SEG_NUM_RE = re.compile(r"seg[_-]?(\d+)")
_LIVE_WAIT_RE = re.compile(r"\[wait\] Waiting for video", re.IGNORECASE)

# Report progress every Nth fragment so a multi-hour stream doesn't fan out
# thousands of progress events. Fragment 1 always reports (state change).
_LIVE_FRAGMENT_REPORT_EVERY = 5


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


def parse_ytdlp_live_progress(line: str) -> dict | None:
    """Parse a yt-dlp line for live-broadcast progress.

    Live streams report fragments, not percent. Returns a dict with a
    pseudo-percent (capped at 99) so the existing progress sink/UI keeps moving,
    plus a human-readable message.
    """
    if not line:
        return None

    if _LIVE_WAIT_RE.search(line):
        return {"percent": 0, "message": "Waiting for stream to start..."}

    if m := _LIVE_FRAG_RE.search(line):
        frag = int(m.group(1))
        # Monotonic pseudo-percent capped at 99 -- never report "complete"
        # mid-stream. The message carries the real signal; percent just keeps
        # the CLI bar visually pinned once we're past the warmup.
        return {"percent": min(99, frag), "message": f"Recording fragment {frag}"}

    if m := _LIVE_HLS_SEG_RE.search(line):
        if seg := _LIVE_SEG_NUM_RE.search(m.group(1)):
            n = int(seg.group(1))
            return {"percent": min(99, n), "message": f"Recording segment {n}"}

    return None


def build_ytdlp_live_command(
    url: str,
    output_path: str,
    *,
    wait_interval: int = 60,
    from_start: bool = True,
) -> list[str]:
    """Build a yt-dlp command for recording a live broadcast (mp4 output).

    Args:
        url: Live broadcast URL.
        output_path: Final output path. Caller is responsible for ensuring
            an .mp4 extension; this function does not coerce.
        wait_interval: Seconds between polls while waiting for the broadcast to
            start. 0 disables waiting.
        from_start: If True, capture the broadcast from its beginning. If False,
            join at the current live edge.

    Why mp4-only: passing `--hls-use-mpegts` or `-o foo.ts` makes yt-dlp 2026.x
    invoke ffmpeg with the full signed HLS manifest URL as a single `-i`
    argument. YouTube's signed URLs are ~2 KB and overflow macOS ARG_MAX,
    aborting with `OSError: Errno 63 File name too long`. mp4 output uses a
    different code path that opens segments individually (short per-segment
    URLs) and remuxes mpegts-in-mp4 -> proper mp4 at clean exit. The
    partial-file-playable property is preserved either way.
    """
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--no-part",
        "--retries", "infinite",
        "--fragment-retries", "infinite",
        "-f", "bv*+ba/b",
        "--remux-video", "mp4",
        "--print", "after_move:filepath",
    ]

    if from_start:
        cmd.append("--live-from-start")
    if wait_interval > 0:
        cmd.extend(["--wait-for-video", str(wait_interval)])

    cmd.extend(["-o", output_path])
    cmd.append(url)
    return cmd


def _run_ytdlp_process(cmd: list[str], on_line) -> tuple[int | None, str | None]:
    """Run a yt-dlp command, forwarding each output line to `on_line`.

    Detects the bare-line filepath emitted by `--print after_move:filepath` and
    returns it alongside the exit code. Translates Ctrl-C into a graceful
    SIGTERM so yt-dlp can finalize/remux any partial output before exiting.
    """
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True
    )
    assert process.stdout is not None

    printed_filepath: str | None = None

    try:
        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if not line:
                continue
            line = line.strip()
            logger.debug("yt-dlp output", line=line)

            # `--print after_move:filepath` emits the resolved path as a bare
            # (non-`[`-prefixed) line after the download finishes.
            if line and not line.startswith("[") and not line.lower().startswith("warning"):
                candidate = Path(line)
                if candidate.is_file():
                    printed_filepath = str(candidate)

            on_line(line)
    except KeyboardInterrupt:
        logger.info("yt-dlp interrupted; terminating gracefully")
        process.terminate()

    process.wait()
    return process.returncode, printed_filepath


def download_live(
    url: str,
    output_path: str,
    *,
    wait_interval: int = 60,
    from_start: bool = True,
) -> str:
    """Record a live broadcast (mp4 output). Reports progress via thread-local sink.

    Throttles fragment progress to one report per N fragments so a multi-hour
    stream doesn't emit thousands of progress events. Returns the path
    to the recorded file. Raises RuntimeError on failure.
    """
    cmd = build_ytdlp_live_command(
        url,
        output_path,
        wait_interval=wait_interval,
        from_start=from_start,
    )
    logger.info("Starting yt-dlp live recording", url=url)

    fragment_count = 0
    last_message = ""

    def on_line(line: str) -> None:
        nonlocal fragment_count, last_message
        progress = parse_ytdlp_live_progress(line)
        if not progress:
            return
        msg = progress["message"]
        if msg.startswith("Recording fragment"):
            fragment_count += 1
            if fragment_count != 1 and fragment_count % _LIVE_FRAGMENT_REPORT_EVERY != 0:
                return
        if msg == last_message:
            return
        report_progress(progress["percent"], msg)
        last_message = msg

    returncode, printed_filepath = _run_ytdlp_process(cmd, on_line)

    if returncode != 0:
        raise RuntimeError(f"yt-dlp live recording failed with exit code {returncode}")

    actual_output = printed_filepath or output_path
    if not Path(actual_output).exists():
        stem = Path(output_path).stem
        parent = Path(output_path).parent
        for candidate in parent.iterdir():
            if candidate.is_file() and candidate.stem == stem:
                actual_output = str(candidate)
                break

    report_progress(100, "Recording complete")
    logger.info("yt-dlp live recording complete", output=actual_output)
    return actual_output


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
    max_duration: int | None = DEFAULT_MAX_DURATION,
) -> str:
    """Download video/audio from a URL. Reports progress via thread-local sink.

    Args:
        url: URL to download.
        output_path: Output file path (may be modified by yt-dlp for audio extraction).
        format_type: 'mp3', 'wav', or 'mp4'.
        max_duration: Max video duration in seconds.

    Returns:
        Path to the downloaded file.

    Raises:
        RuntimeError: If download fails.
    """
    cmd = build_ytdlp_command(url, output_path, format_type, max_duration)
    logger.info("Starting yt-dlp download", url=url, format=format_type)

    def on_line(line: str) -> None:
        progress = parse_ytdlp_progress(line)
        if progress:
            report_progress(
                progress["percent"],
                f"Downloading... {progress['percent']}%"
                + (f" ({progress['speed']})" if progress.get("speed") else ""),
            )

    returncode, printed_filepath = _run_ytdlp_process(cmd, on_line)

    if returncode != 0:
        raise RuntimeError(f"yt-dlp failed with exit code {returncode}")

    actual_output = printed_filepath or output_path
    if not Path(actual_output).exists():
        stem = Path(output_path).stem
        parent = Path(output_path).parent
        for candidate in parent.iterdir():
            if candidate.is_file() and candidate.stem.startswith(stem.split(".")[0]):
                actual_output = str(candidate)
                break

    report_progress(100, "Download complete")
    logger.info("yt-dlp download complete", output=actual_output)
    return actual_output

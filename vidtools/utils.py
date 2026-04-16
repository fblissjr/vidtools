import subprocess
import os
import re
import structlog
from .progress import report_progress, FfmpegError

# --- Logging Setup ---
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

# --- Utility Functions ---

MIN_FFMPEG_VERSION = (5, 1)


def get_ffmpeg_version() -> tuple[int, ...] | None:
    """Return the ffmpeg major.minor version tuple, or None if not found."""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
        match = re.search(r'ffmpeg version (\d+)\.(\d+)', result.stdout)
        if match:
            return (int(match.group(1)), int(match.group(2)))
        # Handle git/nightly builds like "N-12345-gabcdef"
        match = re.search(r'ffmpeg version N-', result.stdout)
        if match:
            return (99, 0)  # Nightly builds are always latest
        return None
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def check_ffmpeg_installed() -> bool:
    """Check if ffmpeg >= 5.1 is installed and in PATH."""
    version = get_ffmpeg_version()
    if version is None:
        return False
    if version < MIN_FFMPEG_VERSION:
        logger.warning("ffmpeg version too old", version=f"{version[0]}.{version[1]}",
                       minimum=f"{MIN_FFMPEG_VERSION[0]}.{MIN_FFMPEG_VERSION[1]}")
        return False
    return True

def is_valid_file(parser, arg):
    """Checks if the provided file path exists and is a file."""
    if not os.path.exists(arg):
        parser.error(f"Error: File '{arg}' not found.")
    if not os.path.isfile(arg):
        parser.error(f"Error: '{arg}' is not a file.")
    return arg


def _parse_ffmpeg_time(time_str: str) -> float:
    """Convert HH:MM:SS.ms time string to seconds."""
    h, m, s_ms = map(float, time_str.split(':'))
    return h * 3600 + m * 60 + s_ms


_PROGRESS_REGEX = re.compile(
    r"frame=\s*\d+\s+fps=\s*[\d\.]+\s+q=[\-\d\.]+\s+size=\s*[\w\d]+\s+time=([\d\:\.]+)\s+bitrate="
)
_DURATION_REGEX = re.compile(r"Duration: (\d{2}:\d{2}:\d{2}\.\d{2})")


def run_ffmpeg(command: list[str]) -> None:
    """Run an ffmpeg command. Report progress via the thread-local sink.

    Raises:
        FfmpegError: when ffmpeg exits non-zero or cannot be invoked.
    """
    logger.debug("FFmpeg command", command=" ".join(command))

    try:
        process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True)
    except FileNotFoundError as e:
        raise FfmpegError("ffmpeg not found. Ensure it is installed and in PATH.") from e

    duration: float | None = None
    stderr_lines: list[str] = []
    last_percent = -1
    assert process.stderr is not None  # we passed stderr=PIPE

    try:
        while True:
            stderr_line = process.stderr.readline()
            if not stderr_line and process.poll() is not None:
                break
            if stderr_line:
                stderr_lines.append(stderr_line)
                if duration is None:
                    duration_match = _DURATION_REGEX.search(stderr_line)
                    if duration_match:
                        duration = _parse_ffmpeg_time(duration_match.group(1))

                progress_match = _PROGRESS_REGEX.search(stderr_line)
                if progress_match and duration:
                    current_time = _parse_ffmpeg_time(progress_match.group(1))
                    percent = int(min(100, (current_time / duration) * 100))
                    # Coalesce: ffmpeg emits a progress line per frame, but we
                    # only need to notify the sink when the integer percent moves.
                    if percent != last_percent:
                        last_percent = percent
                        report_progress(percent, f"Processing... {percent}%")
    except Exception as e:
        process.kill()
        raise FfmpegError(f"FFmpeg execution error: {e}") from e

    if process.returncode != 0:
        error_output = "".join(stderr_lines)
        logger.error("FFmpeg command failed",
                     return_code=process.returncode,
                     error_output=error_output[-500:])
        raise FfmpegError(
            f"FFmpeg failed (code {process.returncode}): {error_output[-500:]}"
        )

    logger.info("FFmpeg command completed successfully")
    report_progress(100, "Complete")

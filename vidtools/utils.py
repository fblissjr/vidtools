import subprocess
import os
import sys
import re
from typing import Callable
from tqdm import tqdm
import structlog

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

def check_ffmpeg_installed():
    """Checks if ffmpeg is installed and in PATH."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except FileNotFoundError:
        return False
    except subprocess.CalledProcessError:
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


def run_ffmpeg_command(command, progress_callback: Callable | None = None):
    """Executes an ffmpeg command with progress tracking and error handling.

    Args:
        command: FFmpeg command as a list of strings.
        progress_callback: Optional callback(percent: int, message: str).
            When provided, sends progress updates to the callback instead of
            using a tqdm progress bar. Used by the web server for job progress.
            When None, uses tqdm (CLI mode).

    Raises:
        RuntimeError: When called with progress_callback and ffmpeg fails
            (instead of calling exit(1) as in CLI mode).
    """
    logger.debug("FFmpeg command", command=" ".join(command))

    process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True)

    # Only create tqdm bar in CLI mode
    progress_bar = None
    if progress_callback is None:
        progress_bar = tqdm(total=100, unit="%", desc="Processing", dynamic_ncols=True)

    duration = None
    progress_regex = re.compile(
        r"frame=\s*\d+\s+fps=\s*[\d\.]+\s+q=[\-\d\.]+\s+size=\s*[\w\d]+\s+time=([\d\:\.]+)\s+bitrate="
    )
    duration_regex = re.compile(r"Duration: (\d{2}:\d{2}:\d{2}\.\d{2})")

    try:
        while True:
            stderr_line = process.stderr.readline()
            if not stderr_line and process.poll() is not None:
                break
            if stderr_line:
                if not duration:
                    duration_match = duration_regex.search(stderr_line)
                    if duration_match:
                        duration = _parse_ffmpeg_time(duration_match.group(1))

                progress_match = progress_regex.search(stderr_line)
                if progress_match and duration:
                    current_time = _parse_ffmpeg_time(progress_match.group(1))
                    progress = min(100, (current_time / duration) * 100)
                    percent = int(progress)

                    if progress_callback:
                        progress_callback(percent, f"Processing... {percent}%")
                    elif progress_bar:
                        progress_bar.update(percent - progress_bar.n)

        if progress_bar:
            progress_bar.close()

        if process.returncode != 0:
            error_output = process.stderr.read()
            logger.error(
                "FFmpeg command failed!",
                return_code=process.returncode,
                error_output=error_output,
            )

            if progress_callback:
                # In job mode, raise instead of exit
                raise RuntimeError(
                    f"FFmpeg failed (code {process.returncode}): {error_output[:500]}"
                )
            else:
                print(f"\nFFmpeg command failed!", file=sys.stderr)
                print(f"Error Code: {process.returncode}", file=sys.stderr)
                print(f"Error Output:\n{error_output}", file=sys.stderr)
                print("\nPlease check the command and your inputs.", file=sys.stderr)
                exit(1)
        else:
            logger.info("FFmpeg command completed successfully.")
            if progress_callback:
                progress_callback(100, "Complete")
            else:
                print("FFmpeg command completed successfully.")

    except FileNotFoundError:
        logger.error("ffmpeg not found!", exc_info=True)
        if progress_callback:
            raise RuntimeError("ffmpeg not found. Ensure it is installed and in PATH.")
        else:
            print("\nError: ffmpeg not found!", file=sys.stderr)
            print("Please ensure ffmpeg is installed and in your PATH.", file=sys.stderr)
            exit(1)

    except RuntimeError:
        # Re-raise RuntimeError from above (don't catch our own errors)
        raise

    except Exception as e:
        logger.exception("An unexpected error occurred during ffmpeg execution")
        if progress_callback:
            raise RuntimeError(f"FFmpeg execution error: {e}")
        else:
            print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
            print(
                "Please review the command and your inputs, and check for ffmpeg errors above.",
                file=sys.stderr,
            )
            exit(1)

import subprocess
import os
import sys
import re
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

def run_ffmpeg_command(command):
    """Executes an ffmpeg command with progress bar and error handling."""
    print("FFmpeg Command:", " ".join(command))  # ADD THIS LINE - Print the command
    process = subprocess.Popen(command, stderr=subprocess.PIPE, universal_newlines=True)
    progress_bar = tqdm(total=100, unit="%", desc="Processing", dynamic_ncols=True)
    duration = None
    progress_regex = re.compile(r"frame=\s*\d+\s+fps=\s*[\d\.]+\s+q=[\-\d\.]+\s+size=\s*[\w\d]+\s+time=([\d\:\.]+)\s+bitrate=")
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
                        duration_str = duration_match.group(1)
                        h, m, s_ms = map(float, duration_str.split(':'))
                        duration = h * 3600 + m * 60 + s_ms
                progress_match = progress_regex.search(stderr_line)
                if progress_match and duration:
                    time_str = progress_match.group(1)
                    h, m, s_ms = map(float, time_str.split(':'))
                    current_time = h * 3600 + m * 60 + s_ms
                    progress = min(100, (current_time / duration) * 100)
                    progress_bar.update(int(progress - progress_bar.n))
        progress_bar.close()
        if process.returncode != 0:
            error_output = process.stderr.read()
            logger.error("FFmpeg command failed!", return_code=process.returncode, error_output=error_output) # Structlog logging
            print(f"\n🚨 FFmpeg command failed! 🚨", file=sys.stderr)
            print(f"Error Code: {process.returncode}", file=sys.stderr)
            print(f"Error Output:\n{error_output}", file=sys.stderr)
            print("\nPlease check the command and your inputs.", file=sys.stderr)
            exit(1)
        else:
            logger.info("FFmpeg command completed successfully.") # Structlog logging
            print("FFmpeg command completed successfully.")
    except FileNotFoundError:
        logger.error("ffmpeg not found!", exc_info=True) # Structlog logging with exception info
        print("\n🚨 Error: ffmpeg not found! 🚨", file=sys.stderr)
        print("Please ensure ffmpeg is installed and in your PATH.", file=sys.stderr)
        exit(1)
    except Exception as e:
        logger.exception("An unexpected error occurred during ffmpeg execution") # Full exception logging
        print(f"\n🚨 An unexpected error occurred: {e} 🚨", file=sys.stderr)
        print("Please review the command and your inputs, and check for ffmpeg errors above.", file=sys.stderr)
        exit(1)
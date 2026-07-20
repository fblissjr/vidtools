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

# --- Media-type classification by extension (single source of truth) ---
# Used by CLI smart dispatch (_detect_kind) and the audio-op video-aware remux.
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".wmv",
              ".m4v", ".mpg", ".mpeg", ".gif"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".opus", ".m4a", ".wma"}
# Must stay a superset of what `image_ops._FORMAT_MODES` can write, minus `.gif`
# (a still-encoder target that dispatches as video, since an animated GIF is a
# video to every other verb). `tests/test_imaging_contract.py` pins that
# invariant: `.avif` was writable via `compress --allow-avif` but absent here,
# so the tool emitted files it then refused to open.
#
# `.mpo` is read-only -- Pillow decodes it and `_open_image` handles its
# is_animated quirk, but nothing writes it. It is here because real iPhone
# photos arrive as MPO, not because anything produces one.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif",
              ".avif", ".mpo"}

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


# --- ffmpeg build capabilities ---
#
# One probe for every capability question, next to check_ffmpeg_installed.
# Homebrew's slim formula omits libass, libaom-av1 and libvorbis, so several
# features need to ask; separate hand-rolled probes would drift apart.

#: Populated per capability kind on the first successful probe. A *failed* probe
#: is deliberately not cached: an lru_cache would pin a transient timeout for the
#: process lifetime, leaving a capability reported as missing until restart.
_CAPABILITY_CACHE: dict[str, set[str]] = {}


def _ffmpeg_capabilities(kind: str) -> set[str] | None:
    """Names ffmpeg exposes for `kind` ("filters" or "encoders").

    Returns None if the probe itself failed -- which is not "none available".
    Callers must distinguish: reporting a missing binary as a missing library
    sends the user to the wrong fix.

    One probe for every capability question. Homebrew's slim formula omits
    libass, libaom-av1 and libvorbis, so three separate features need to ask
    this, and three hand-rolled subprocess probes would drift apart.
    """
    if kind in _CAPABILITY_CACHE:
        return _CAPABILITY_CACHE[kind]
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", f"-{kind}"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = set()
    for line in out.splitlines():
        parts = line.split()
        # "  T.. scale  V->V  Scale ..." / " V..... libx264  H.264 ..."
        # -- the name is the second field in both listings.
        if len(parts) >= 2 and parts[0] not in ("Filters:", "Encoders:", "="):
            names.add(parts[1])
    _CAPABILITY_CACHE[kind] = names
    return names


def _ffmpeg_has_filter(name: str) -> bool | None:
    """True/False if known, None if the build could not be probed."""
    filters = _ffmpeg_capabilities("filters")
    return None if filters is None else name in filters


def _ffmpeg_has_encoder(name: str) -> bool | None:
    """True/False if known, None if the build could not be probed."""
    encoders = _ffmpeg_capabilities("encoders")
    return None if encoders is None else name in encoders


#: Encoders that Homebrew's slim `ffmpeg` formula omits, with what to do about
#: it. Anything absent here fails with ffmpeg's own error, which is right for a
#: genuinely exotic codec but wrong for these three -- they are missing on a
#: default macOS install, so the failure is common rather than exceptional.
_ENCODER_HINTS = {
    "libaom-av1": "AV1 encoding needs libaom-av1",
    "libvorbis": "Ogg Vorbis output needs libvorbis",
    "libx265": "HEVC encoding needs libx265",
}


def require_encoder(name: str) -> None:
    """Raise a message naming the gap before ffmpeg emits a cryptic one.

    Only the encoders in `_ENCODER_HINTS` are probed. Probing every encode
    would add an `ffmpeg -encoders` spawn to every convert/resize/trim that
    re-encodes -- a subprocess and up to a 15s timeout budget, to confirm
    libx264 exists. The risky set is small and known: it is exactly the
    encoders Homebrew's slim formula omits.
    """
    if name not in _ENCODER_HINTS:
        return
    if _ffmpeg_has_encoder(name) is not False:
        return  # present, or unprobeable -- let ffmpeg speak for itself
    hint = _ENCODER_HINTS.get(name, f"This output needs the '{name}' encoder")
    raise FfmpegError(
        f"{hint}, which this ffmpeg was built without. On macOS: "
        "'brew install ffmpeg-full' (the plain 'ffmpeg' formula carries ten "
        "dependencies and omits it)."
    )

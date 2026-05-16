#!/usr/bin/env python3
"""Record a YouTube live broadcast or any HLS live URL via yt-dlp.

Designed for shows that are only streamed live. Output is always mp4 (see
build_command's note for why .ts is not exposed on macOS).

Conservative defaults:
- Poll scheduled streams every 120 seconds
- Record from the start of the broadcast (--live-from-start)
- Avoid noisy debug/network output unless --debug is requested
- Start once, wait politely, record, and finish

Usage:
    uv run examples/record_live.py <url> [output_basename] [options]

Recommended:
    # Scheduled stream, polite default behavior
    uv run examples/record_live.py "https://youtube.com/@channel/live" show.mp4

    # Already live, capture from the broadcast's start
    uv run examples/record_live.py "https://youtube.com/@channel/live" show.mp4 --no-wait

    # Already live, join at the current live edge (no backfill)
    uv run examples/record_live.py "https://youtube.com/@channel/live" show.mp4 --no-wait --from-now

    # Verbose yt-dlp output for diagnosing problems
    uv run examples/record_live.py "https://youtube.com/@channel/live" show.mp4 --debug

Requires:
    yt-dlp, ffmpeg, and ffprobe in PATH (yt-dlp uses ffmpeg for HLS download
    and for the final remux).
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_WAIT_SECONDS = 120
LOW_WAIT_WARNING_SECONDS = 30


# Live broadcasts emit fragment-style progress, not "[download] X% of YMiB".
# Watch for these lines to count downloaded fragments.
_FRAG_HLS_RE = re.compile(r"\[hls\b.*\bOpening\b.*'([^']+)'")
_FRAG_DOWNLOAD_RE = re.compile(r"\[download\] Downloading fragment (\d+)")
_WAIT_RE = re.compile(r"\[wait\] Waiting for video", re.IGNORECASE)


def build_command(
    url: str,
    output_path: Path,
    *,
    wait_interval: int,
    from_start: bool,
    debug: bool = False,
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
) -> list[str]:
    """Build the yt-dlp command for live recording.

    Output is always mp4. See the note inside on why ts is not supported.

    Args:
        url:
            YouTube or any supported live URL.

        output_path:
            Final file path. Must be .mp4.

        wait_interval:
            Seconds between polls while waiting for the stream to go live.
            0 disables waiting.

        from_start:
            If True, ask yt-dlp to download from the start of the broadcast.
            If False, join wherever the stream currently is.

        debug:
            Enables yt-dlp verbose output.

        cookies_from_browser:
            Browser name for yt-dlp --cookies-from-browser.
            Examples: chrome, firefox, edge, brave, safari.

        cookies_file:
            Path to a cookies.txt file for yt-dlp --cookies.
    """
    cmd: list[str] = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--no-part",
        "--retries",
        "infinite",
        "--fragment-retries",
        "infinite",
        # Live formats: prefer separate video+audio for best quality; the
        # broadcaster picks the rungs we can hit.
        "-f",
        "bv*+ba/b",
        # Remux the final container to mp4. For DASH-served streams this
        # combines the split video/audio files; for HLS it normalizes the
        # mpegts-in-mp4 download into a proper mp4. Cheap and lossless.
        "--remux-video",
        "mp4",
        "--print",
        "after_move:filepath",
    ]
    # NOTE on output container: mp4 only. Two reasons we don't expose .ts:
    #
    # 1) `--hls-use-mpegts` and `-o output.ts` both make yt-dlp 2026.x invoke
    #    ffmpeg with the full signed HLS master playlist URL as a single `-i`
    #    argument. YouTube's signed URLs run ~2 KB and overflow macOS
    #    ARG_MAX, aborting with `OSError: Errno 63 File name too long`.
    # 2) yt-dlp's default HLS downloader writes mpegts data into the .mp4
    #    file during recording (FixupM3u8 converts to real mp4 at clean
    #    exit), so the partial-file-is-playable property is preserved
    #    without forcing .ts.

    if debug:
        cmd.append("--verbose")

    if cookies_from_browser:
        cmd.extend(["--cookies-from-browser", cookies_from_browser])

    if cookies_file:
        cmd.extend(["--cookies", str(cookies_file)])

    if from_start:
        cmd.append("--live-from-start")

    if wait_interval > 0:
        cmd.extend(["--wait-for-video", str(wait_interval)])

    cmd.extend(["-o", str(output_path)])
    cmd.append(url)

    return cmd


def parse_progress(line: str) -> str | None:
    """Return a one-line status string for a yt-dlp output line, or None."""
    if _WAIT_RE.search(line):
        return "Waiting for stream to start..."

    if m := _FRAG_DOWNLOAD_RE.search(line):
        return f"Recording fragment {m.group(1)}"

    if m := _FRAG_HLS_RE.search(line):
        # Fragment filenames often look like ".../seg-1234-...ts".
        # Pull the number if present.
        seg_match = re.search(r"seg[_-]?(\d+)", m.group(1))
        if seg_match:
            return f"Recording segment {seg_match.group(1)}"

    return None


def quote_cmd(cmd: list[str]) -> str:
    """Return a shell-readable command string for logging."""
    return " ".join(shlex.quote(part) for part in cmd)


def warn_if_aggressive_settings(wait_interval: int, no_wait: bool) -> None:
    """Warn if settings may poll more often than necessary."""
    if no_wait:
        print(
            "[record_live] no-wait mode enabled: this will check once and fail if the stream is not live.",
            file=sys.stderr,
        )
        return

    if wait_interval == 0:
        print(
            "[record_live] wait is 0: this will check once and fail if the stream is not live.",
            file=sys.stderr,
        )
        return

    if wait_interval < LOW_WAIT_WARNING_SECONDS:
        print(
            f"[record_live] warning: --wait {wait_interval} is very frequent. "
            f"Consider --wait {DEFAULT_WAIT_SECONDS} or higher for scheduled streams.",
            file=sys.stderr,
        )


def record(
    url: str,
    output_path: Path,
    *,
    wait_interval: int,
    from_start: bool,
    debug: bool = False,
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
) -> Path:
    """Record a live stream and return the final output path."""
    if shutil.which("yt-dlp") is None:
        raise SystemExit(
            "yt-dlp not found in PATH. Install it first, for example:\n"
            "  brew install yt-dlp\n"
            "or:\n"
            "  python3 -m pip install -U yt-dlp"
        )

    missing_ffmpeg_tools = [
        tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None
    ]
    if missing_ffmpeg_tools:
        raise SystemExit(
            "Recording requires ffmpeg and ffprobe (yt-dlp uses them to "
            "download HLS segments and remux to mp4).\n"
            f"Missing: {', '.join(missing_ffmpeg_tools)}\n"
            "Install with: brew install ffmpeg"
        )

    if cookies_file is not None and not cookies_file.exists():
        raise SystemExit(f"Cookies file does not exist: {cookies_file}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = build_command(
        url,
        output_path,
        wait_interval=wait_interval,
        from_start=from_start,
        debug=debug,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_file,
    )

    print("[record_live] polite mode: start once, poll slowly, record normally.", file=sys.stderr)
    print(f"[record_live] wait interval: {wait_interval} seconds", file=sys.stderr)
    print(
        f"[record_live] from start: {'yes' if from_start else 'no, from current live point'}",
        file=sys.stderr,
    )
    print(f"[record_live] cmd: {quote_cmd(cmd)}", file=sys.stderr)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None

    final_path: Path | None = None
    last_status = ""

    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip()

            if not line:
                continue

            # By default, only show simplified status messages.
            # With --debug, echo every yt-dlp line.
            if debug:
                print(f"[yt-dlp] {line}", file=sys.stderr)

            # --print after_move:filepath emits the resolved path as a bare line.
            # Avoid treating normal bracketed yt-dlp logs as paths.
            if not line.startswith("[") and not line.lower().startswith("warning"):
                candidate = Path(line)
                if candidate.exists():
                    final_path = candidate

            status = parse_progress(line)
            if status and status != last_status:
                print(f"[record_live] {status}", file=sys.stderr)
                last_status = status

    except KeyboardInterrupt:
        # Graceful stop. yt-dlp should finish the current fragment and remux if
        # --remux-video is set.
        print("[record_live] stopping Ctrl-C; finalizing...", file=sys.stderr)
        proc.terminate()

    proc.wait()

    if proc.returncode not in (0, None):
        raise SystemExit(f"yt-dlp exited with code {proc.returncode}")

    if final_path is None:
        # Fall back to scanning the directory for our output stem.
        stem = output_path.stem

        for cand in output_path.parent.iterdir():
            if cand.is_file() and cand.stem == stem:
                final_path = cand
                break

    if final_path is None or not final_path.exists():
        raise SystemExit("Stream ended but no output file was found.")

    # stdout: final path, useful for piping into another command.
    print(str(final_path))

    return final_path


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
    )

    p.add_argument(
        "url",
        help="Live broadcast URL",
    )

    p.add_argument(
        "output",
        nargs="?",
        default="live_recording.mp4",
        help="Output path. Default: live_recording.mp4. Non-mp4 suffixes are "
             "coerced to .mp4 (see build_command docstring for why).",
    )

    p.add_argument(
        "--wait",
        type=int,
        default=DEFAULT_WAIT_SECONDS,
        help=(
            "Seconds between polls while waiting for the stream to start. "
            f"Default: {DEFAULT_WAIT_SECONDS}. "
            "Use 0 to check once and fail immediately if not live."
        ),
    )

    p.add_argument(
        "--no-wait",
        action="store_true",
        help="Equivalent to --wait 0. Use this only when the stream is already live.",
    )

    p.add_argument(
        "--from-now",
        action="store_true",
        help="Join the stream at its current position instead of recording from the start.",
    )

    p.add_argument(
        "--debug",
        action="store_true",
        help="Pass --verbose to yt-dlp and echo raw yt-dlp output.",
    )

    p.add_argument(
        "--cookies-from-browser",
        metavar="BROWSER",
        help=(
            "Use cookies from a browser profile for restricted streams. "
            "Examples: chrome, firefox, edge, brave, safari."
        ),
    )

    p.add_argument(
        "--cookies",
        type=Path,
        metavar="COOKIES_TXT",
        help="Use a cookies.txt file for restricted streams.",
    )

    args = p.parse_args()

    if args.wait < 0:
        raise SystemExit("--wait must be >= 0")

    if args.cookies_from_browser and args.cookies:
        raise SystemExit("Use either --cookies-from-browser or --cookies, not both.")

    wait_interval = 0 if args.no_wait else args.wait

    warn_if_aggressive_settings(
        wait_interval=wait_interval,
        no_wait=args.no_wait,
    )

    output_path = Path(args.output)
    if output_path.suffix.lower() != ".mp4":
        coerced = output_path.with_suffix(".mp4")
        print(
            f"[record_live] coercing output {output_path} -> {coerced} (mp4 only)",
            file=sys.stderr,
        )
        output_path = coerced

    record(
        url=args.url,
        output_path=output_path,
        wait_interval=wait_interval,
        from_start=not args.from_now,
        debug=args.debug,
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies,
    )


if __name__ == "__main__":
    main()

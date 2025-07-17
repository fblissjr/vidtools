import argparse
from typing import Any
from utils import is_valid_file                    # helper for path checks
import main                                        # every sub-command delegates to main

# ──────────────────────────────────────────────────────────────────────────────
def setup_argparse() -> argparse.ArgumentParser:
    """Return the top-level argument parser with all sub-commands registered."""
    parser = argparse.ArgumentParser(
        description="vidtools – FFmpeg CLI with presets & helpers",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        title="commands",
        dest="command",
        help="Available operations",
    )

    # ------------------------------------------------------------------ resize
    resize_parser = subparsers.add_parser(
        "resize",
        help="Resize video resolution",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    resize_parser.add_argument(
        "input", type=lambda x: is_valid_file(resize_parser, x), help="Input video"
    )
    resize_parser.add_argument("output", help="Resized output video")
    grp = resize_parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("-p", "--percentage", type=float, help="Scale by factor (e.g. 0.5)")
    grp.add_argument("-W", "--width", type=int, help="Target width")
    grp.add_argument("-H", "--height", type=int, help="Target height")
    resize_parser.add_argument(
        "--algorithm",
        default="lanczos",
        choices=[
            "neighbor",
            "fast_bilinear",
            "bilinear",
            "bicubic",
            "experimental",
            "area",
            "bicubiclina",
            "gauss",
            "sinc",
            "lanczos",
            "spline",
        ],
        help="FFmpeg scale flags (default: lanczos)",
    )
    resize_parser.set_defaults(func=main.resize_video_handler)


    # ───── sanitize ─────
    sanitize = subparsers.add_parser(
        "sanitize",
        help="Auto-crop bottom banner, add light noise, strip metadata/SEI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sanitize.add_argument("input",  type=lambda x: is_valid_file(sanitize, x))
    sanitize.add_argument("output")
    sanitize.add_argument("--limit", type=int, default=24,
                          help="cropdetect limit (default 24)")
    sanitize.add_argument("--noise", type=int, default=6,
            help="Noise amplitude 0-100 (default 6)")
    sanitize.add_argument(
            "--extra-bottom", type=int, default=0,
            metavar="PX",
            help="Crop this many extra pixels off the bottom AFTER auto-detect")
    sanitize.add_argument("--crf",   type=int, default=22,
                          help="x264 CRF quality (default 22)")
    sanitize.add_argument("--aac", action="store_true",
                          help="Re-encode audio to AAC 128 kb/s (default: copy)")
    sanitize.add_argument("--no-audio", action="store_true",
                          help="Strip audio stream entirely")
    sanitize.set_defaults(func=main.sanitize_video_handler)

    return parser

_parser: argparse.ArgumentParser | None = None
def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    global _parser
    if _parser is None:
        _parser = setup_argparse()
    return _parser.parse_args(argv)

def handle_command(args: argparse.Namespace) -> None:
    if args.command:
        args.func(args)
    else:
        _parser.print_help()

if __name__ == "__main__":
    handle_command(parse_arguments())

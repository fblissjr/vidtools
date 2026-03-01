"""
vidtools - FFmpeg + yt-dlp video processing tool with web UI and CLI.
"""

from ._version import __version__, __version_info__
__author__ = "Fred Bliss"

# Import core functions from the operations module
from .operations import (
    cut_video,
    resize_video,
    convert_format,
    extract_audio,
    extract_frames,
    concatenate_videos,
    crop_video,
    rotate_video,
    add_subtitles,
    get_video_info,
    sanitize_video,
    apply_preset,
    create_slideshow,
)

# Import preset management functions
from .presets import (
    get_presets,
    save_preset_command,
    delete_preset,
    edit_preset_command,
    list_presets,
)

# Define what is available for public import
__all__ = [
    "__version__",
    "cut_video",
    "resize_video",
    "convert_format",
    "extract_audio",
    "extract_frames",
    "concatenate_videos",
    "crop_video",
    "rotate_video",
    "add_subtitles",
    "get_video_info",
    "sanitize_video",
    "apply_preset",
    "create_slideshow",
    "get_presets",
    "save_preset_command",
    "delete_preset",
    "edit_preset_command",
    "list_presets",
]

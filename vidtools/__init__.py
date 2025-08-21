"""
vidtools - A comprehensive FFmpeg wrapper for common video processing tasks.
"""

from ._version import __version__, __version_info__

__author__ = "Fred Bliss"

from .main import (
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
)

from .presets import (
    get_presets,
    save_preset_command,
    delete_preset,
    edit_preset_command,
    list_presets,
)

__all__ = [
    # Version
    "__version__",
    # Core functions
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
    # Preset functions
    "get_presets",
    "save_preset_command",
    "delete_preset",
    "edit_preset_command",
    "list_presets",
]
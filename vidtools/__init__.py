"""
vidtools - FFmpeg + PIL + yt-dlp media processing CLI.
"""

from ._version import __version__, __version_info__
__author__ = "Fred Bliss"

# Import core functions from operations modules
from .video_ops import (
    cut_video,
    resize_video,
    convert_format,
    extract_audio,
    extract_frames,
    screenshot,
    concatenate_videos,
    crop_video,
    rotate_video,
    add_subtitles,
    get_video_info,
    sanitize_video,
    apply_preset,
    create_slideshow,
    add_audio,
    create_srt_from_transcript,
    trim_to_audio,
)

from .image_ops import (
    resize_image,
    convert_image,
    crop_image,
    create_thumbnail,
    strip_image_metadata,
    optimize_image,
    adjust_image,
    get_image_info,
)

from .compress import (
    Candidate,
    CompressionReport,
    available_formats,
    compress_image,
    compute_ssim,
    recommend,
)

from .audio_ops import (
    convert_audio,
    cut_audio,
    normalize_audio,
    mix_audio,
    get_audio_info,
    fade_audio,
    resample_audio,
    detect_silence,
    remove_silence,
    clean_audio,
)

# Import preset management functions
from .presets import (
    get_presets,
    save_preset_command,
    delete_preset,
    edit_preset_command,
    list_presets,
)

__all__ = [
    "__version__",
    # Video
    "cut_video", "resize_video", "convert_format", "extract_audio",
    "extract_frames", "screenshot", "concatenate_videos", "crop_video", "rotate_video",
    "add_subtitles", "get_video_info", "sanitize_video", "apply_preset",
    "create_slideshow", "add_audio", "create_srt_from_transcript",
    "trim_to_audio",
    # Image
    "resize_image", "convert_image", "crop_image", "create_thumbnail",
    "strip_image_metadata", "optimize_image", "adjust_image", "get_image_info",
    # compression search
    "compress_image", "recommend", "compute_ssim", "available_formats",
    "Candidate", "CompressionReport",
    # Audio
    "convert_audio", "cut_audio", "normalize_audio", "mix_audio",
    "get_audio_info", "fade_audio", "resample_audio",
    "detect_silence", "remove_silence", "clean_audio",
    # Presets
    "get_presets", "save_preset_command", "delete_preset",
    "edit_preset_command", "list_presets",
]

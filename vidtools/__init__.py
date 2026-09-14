"""
vidtools - FFmpeg + PIL + yt-dlp media processing CLI.
"""

from ._version import __version__, __version_info__

__author__ = "Fred Bliss"

# Import core functions from operations modules
from .audio_ops import (
    clean_audio,
    convert_audio,
    cut_audio,
    fade_audio,
    get_audio_info,
    normalize_audio,
    resample_audio,
)
from .compress import (
    Candidate,
    CompressionReport,
    available_formats,
    compress_image,
    compute_ssim,
    recommend,
)
from .image_ops import (
    adjust_image,
    convert_image,
    create_thumbnail,
    crop_image,
    get_image_info,
    optimize_image,
    resize_image,
    strip_image_metadata,
)
from .video_ops import (
    add_audio,
    add_subtitles,
    concatenate_videos,
    convert_format,
    create_slideshow,
    create_srt_from_transcript,
    crop_video,
    cut_video,
    extract_audio,
    extract_frames,
    get_video_info,
    resize_video,
    rotate_video,
    sanitize_video,
    screenshot,
    trim_to_audio,
)

__all__ = [
    "__version__",
    # Video
    "cut_video", "resize_video", "convert_format", "extract_audio",
    "extract_frames", "screenshot", "concatenate_videos", "crop_video", "rotate_video",
    "add_subtitles", "get_video_info", "sanitize_video",
    "create_slideshow", "add_audio", "create_srt_from_transcript",
    "trim_to_audio",
    # Image
    "resize_image", "convert_image", "crop_image", "create_thumbnail",
    "strip_image_metadata", "optimize_image", "adjust_image", "get_image_info",
    # compression search
    "compress_image", "recommend", "compute_ssim", "available_formats",
    "Candidate", "CompressionReport",
    # Audio
    "convert_audio", "cut_audio", "normalize_audio",
    "get_audio_info", "fade_audio", "resample_audio", "clean_audio",
]

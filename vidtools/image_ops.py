"""Image operations using PIL/Pillow."""

import os
from PIL import Image, ImageOps, ImageEnhance

from .utils import logger


def resize_image(input_file: str, output_file: str, percentage: float | None = None,
                 width: int | None = None, height: int | None = None,
                 algorithm: str = "lanczos"):
    """Resize an image by percentage, width, or height (preserving aspect ratio)."""
    resampling = {
        "lanczos": Image.Resampling.LANCZOS,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "nearest": Image.Resampling.NEAREST,
    }.get(algorithm, Image.Resampling.LANCZOS)

    with Image.open(input_file) as img:
        if percentage:
            new_w = int(img.width * percentage)
            new_h = int(img.height * percentage)
        elif width:
            new_w = width
            new_h = int(img.height * (width / img.width))
        elif height:
            new_h = height
            new_w = int(img.width * (height / img.height))
        else:
            raise ValueError("You must specify a percentage, width, or height.")

        result = img.resize((new_w, new_h), resampling)
        result.save(output_file)

    logger.info("Image resized", output=output_file, size=f"{new_w}x{new_h}")


def convert_image(input_file: str, output_file: str, quality: int = 85):
    """Convert image format (auto-detected from output extension)."""
    with Image.open(input_file) as img:
        # Convert to RGB if saving to JPEG (no alpha channel)
        if output_file.lower().endswith((".jpg", ".jpeg")) and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        save_kwargs = {}
        if output_file.lower().endswith((".jpg", ".jpeg", ".webp")):
            save_kwargs["quality"] = quality
        if output_file.lower().endswith(".png"):
            save_kwargs["optimize"] = True
        img.save(output_file, **save_kwargs)

    logger.info("Image converted", output=output_file)


def crop_image(input_file: str, output_file: str, width: int, height: int,
               x: int = 0, y: int = 0):
    """Crop an image to the specified dimensions."""
    with Image.open(input_file) as img:
        if x + width > img.width or y + height > img.height:
            raise ValueError(
                f"Crop region ({x},{y},{width},{height}) exceeds image bounds ({img.width}x{img.height})")
        result = img.crop((x, y, x + width, y + height))
        result.save(output_file)

    logger.info("Image cropped", output=output_file, size=f"{width}x{height}")


def create_thumbnail(input_file: str, output_file: str, size: int = 256):
    """Create an aspect-preserving thumbnail."""
    with Image.open(input_file) as img:
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        img.save(output_file)

    logger.info("Thumbnail created", output=output_file, max_size=size)


def strip_image_metadata(input_file: str, output_file: str):
    """Remove all metadata (EXIF, IPTC, XMP) from an image."""
    with Image.open(input_file) as img:
        # Create a clean copy with no metadata
        clean = Image.new(img.mode, img.size)
        clean.paste(img)
        clean.save(output_file)

    logger.info("Image metadata stripped", output=output_file)


def optimize_image(input_file: str, output_file: str, quality: int = 80,
                   max_width: int | None = None, max_height: int | None = None):
    """Resize-to-fit and compress an image."""
    with Image.open(input_file) as img:
        if max_width or max_height:
            max_w = max_width or img.width
            max_h = max_height or img.height
            img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)

        # Convert to RGB for JPEG output
        if output_file.lower().endswith((".jpg", ".jpeg")) and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        save_kwargs = {"optimize": True}
        if output_file.lower().endswith((".jpg", ".jpeg", ".webp")):
            save_kwargs["quality"] = quality
        img.save(output_file, **save_kwargs)

    logger.info("Image optimized", output=output_file)


def adjust_image(input_file: str, output_file: str,
                 brightness: float | None = None, contrast: float | None = None,
                 rotation: int | None = None, flip: str | None = None):
    """Adjust image brightness, contrast, rotation, or flip."""
    with Image.open(input_file) as img:
        if brightness is not None:
            img = ImageEnhance.Brightness(img).enhance(brightness)
        if contrast is not None:
            img = ImageEnhance.Contrast(img).enhance(contrast)
        if rotation is not None:
            img = img.rotate(-rotation, expand=True)  # negative for CW rotation
        if flip == "horizontal":
            img = ImageOps.mirror(img)
        elif flip == "vertical":
            img = ImageOps.flip(img)
        elif flip is not None:
            raise ValueError(f"Invalid flip: '{flip}'. Use 'horizontal' or 'vertical'.")
        img.save(output_file)

    logger.info("Image adjusted", output=output_file)


def get_image_info(input_file: str) -> dict:
    """Return image metadata as a dict."""
    with Image.open(input_file) as img:
        return {
            "width": img.width,
            "height": img.height,
            "format": img.format,
            "mode": img.mode,
            "file_size": os.path.getsize(input_file),
        }

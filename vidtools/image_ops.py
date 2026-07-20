"""Image operations using PIL/Pillow.

Every op goes through `_open_image` / `_save_image` rather than calling
`Image.open` / `.save` directly. That indirection is the point of this module's
shape: six defects here were all instances of the same omission, which is why
they were mutually inconsistent (`convert` guarded colour mode, `resize` didn't;
PNG kept its ICC profile, JPEG didn't; `strip_image_metadata` turned palette
images black). Handling open and save in one place makes the next such bug hard
to write.

**Metadata policy.** "Metadata" is three things with three different defaults:

* *Rendering-critical* -- EXIF Orientation and the ICC profile. These are part
  of the image, not annotations on it. Orientation is baked into pixels on open
  so the tag stops being load-bearing; the ICC profile is carried through to the
  output. Never optional.
* *Identifying* -- GPS, Make, Model, DateTimeOriginal. Stripped by default.
  These ops write a *new* file, so the original keeps everything; a leaked
  location does not come back.
* *Provenance* -- Copyright, Artist, ImageDescription. Preserved only when the
  caller passes `preserve_metadata=True`.

This deliberately differs from macOS's Convert Image, which preserves by
default. vidtools is dominated by compress-to-share, where the failure mode is
publishing coordinates nobody meant to publish.
"""

import os
from typing import Any

from PIL import Image, ImageCms, ImageEnhance, ImageOps

from .utils import logger

#: EXIF tags kept when `preserve_metadata=True`. Everything else is dropped:
#: an allowlist fails closed, so a tag nobody classified is not a leak.
_PROVENANCE_TAGS = {
    0x010E: "ImageDescription",
    0x013B: "Artist",
    0x8298: "Copyright",
    0x00FE: "NewSubfileType",
}

_JPEG_EXTS = (".jpg", ".jpeg")

#: Formats with no alpha channel; RGBA/LA/P inputs are flattened before saving.
_NO_ALPHA_EXTS = _JPEG_EXTS

#: Extensions whose encoder takes a `quality` argument. AVIF's omission here was
#: a silent bug: quality=1 and quality=100 produced byte-identical files.
_QUALITY_EXTS = (*_JPEG_EXTS, ".webp", ".avif")


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _open_image(input_file: str) -> Image.Image:
    """Open an image with orientation applied and the colour profile retained.

    `Image.open` does not apply EXIF Orientation, so a phone photo arrives as a
    landscape buffer with a tag saying "display me rotated". Any geometry op
    then works on the wrong pixels -- `crop_image` cropped a region the user
    could not see. Applying it here makes the buffer match what the user was
    looking at, and lets the tag be dropped safely afterwards.

    `exif_transpose` clears the tag it consumes, so the caller must not
    re-attach the source EXIF wholesale.
    """
    with Image.open(input_file) as handle:
        img = ImageOps.exif_transpose(handle)
        if img is None:  # pragma: no cover - only if the file has no EXIF path
            img = handle.copy()
        # exif_transpose drops info keys on some paths; carry the profile over.
        if "icc_profile" in handle.info:
            img.info["icc_profile"] = handle.info["icc_profile"]
        return img


def _build_exif(img: Image.Image, preserve_metadata: bool) -> bytes | None:
    """Return the EXIF block to write, or None to write none at all.

    Orientation is deliberately absent: `_open_image` already baked it into the
    pixels, so re-emitting it would rotate the image a second time.
    """
    if not preserve_metadata:
        return None
    source = img.getexif()
    if not source:
        return None
    kept = Image.Exif()
    for tag in _PROVENANCE_TAGS:
        if tag in source:
            kept[tag] = source[tag]
    return kept.tobytes() if kept else None


def _save_image(img: Image.Image, output_file: str, quality: int | None = None,
                preserve_metadata: bool = False, **extra) -> None:
    """Save an image with format-correct options and the metadata policy applied.

    Handles the three things every call site used to handle differently, or not
    at all: flattening alpha for formats that cannot store it, carrying the ICC
    profile (Pillow's PNG and AVIF plugins fall back to `im.info`, but its JPEG
    and WebP plugins read the save kwarg only), and passing `quality` to every
    encoder that accepts one.
    """
    ext = _ext(output_file)
    save_kwargs: dict[str, Any] = dict(extra)

    if ext in _NO_ALPHA_EXTS and img.mode not in ("RGB", "L"):
        # Flatten onto white rather than converting straight to RGB: a bare
        # convert composites transparent pixels onto black.
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            backdrop = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            backdrop.paste(rgba, mask=rgba.split()[-1])
            img = backdrop
        else:
            img = img.convert("RGB")

    if quality is not None and ext in _QUALITY_EXTS:
        save_kwargs["quality"] = quality
        # WebP at the top of the scale means lossless (matches "higher = better").
        if ext == ".webp" and quality >= 100:
            save_kwargs["lossless"] = True

    if ext == ".png":
        save_kwargs.setdefault("optimize", True)

    profile = img.info.get("icc_profile")
    if profile:
        save_kwargs["icc_profile"] = profile

    exif = _build_exif(img, preserve_metadata)
    if exif:
        save_kwargs["exif"] = exif

    img.save(output_file, **save_kwargs)


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def resize_image(input_file: str, output_file: str, percentage: float | None = None,
                 width: int | None = None, height: int | None = None,
                 algorithm: str = "lanczos", preserve_metadata: bool = False):
    """Resize an image by percentage, width, or height (preserving aspect ratio)."""
    resampling = {
        "lanczos": Image.Resampling.LANCZOS,
        "bilinear": Image.Resampling.BILINEAR,
        "bicubic": Image.Resampling.BICUBIC,
        "nearest": Image.Resampling.NEAREST,
    }.get(algorithm, Image.Resampling.LANCZOS)

    img = _open_image(input_file)
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
    result.info = img.info
    _save_image(result, output_file, preserve_metadata=preserve_metadata)

    logger.info("Image resized", output=output_file, size=f"{new_w}x{new_h}")


def convert_image(input_file: str, output_file: str, quality: int = 85,
                  preserve_metadata: bool = False):
    """Convert image format (auto-detected from output extension)."""
    img = _open_image(input_file)
    _save_image(img, output_file, quality=quality, preserve_metadata=preserve_metadata)

    logger.info("Image converted", output=output_file)


def crop_image(input_file: str, output_file: str, width: int, height: int,
               x: int = 0, y: int = 0, preserve_metadata: bool = False):
    """Crop an image to the specified dimensions.

    Coordinates are relative to the image as displayed, not the raw buffer --
    `_open_image` has already applied EXIF orientation, so a region picked off a
    portrait phone photo lands where the user expects.
    """
    img = _open_image(input_file)
    if x < 0 or y < 0:
        raise ValueError(f"Crop origin ({x},{y}) must be non-negative")
    if x + width > img.width or y + height > img.height:
        raise ValueError(
            f"Crop region ({x},{y},{width},{height}) exceeds image bounds "
            f"({img.width}x{img.height})")
    result = img.crop((x, y, x + width, y + height))
    result.info = img.info
    _save_image(result, output_file, preserve_metadata=preserve_metadata)

    logger.info("Image cropped", output=output_file, size=f"{width}x{height}")


def create_thumbnail(input_file: str, output_file: str, size: int = 256,
                     preserve_metadata: bool = False):
    """Create an aspect-preserving thumbnail. Never upscales."""
    img = _open_image(input_file)
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    _save_image(img, output_file, preserve_metadata=preserve_metadata)

    logger.info("Thumbnail created", output=output_file, max_size=size)


def strip_image_metadata(input_file: str, output_file: str):
    """Remove metadata from an image without altering a single pixel.

    The previous implementation built `Image.new(img.mode, img.size)` and pasted
    into it. For a palette image that allocates a *default* palette, and pasting
    between two P-mode images copies raw indices without remapping -- so the
    output was the source's indices read against an unrelated colour table, and
    a four-colour PNG came out solid black.

    Copying the image and clearing its info dict keeps the palette attached.
    """
    img = _open_image(input_file)
    clean = img.copy()
    clean.info = {}
    if img.mode == "P":
        palette = img.getpalette()
        if palette:
            clean.putpalette(palette)
    clean.save(output_file)

    logger.info("Image metadata stripped", output=output_file)


def optimize_image(input_file: str, output_file: str, quality: int = 80,
                   max_width: int | None = None, max_height: int | None = None,
                   preserve_metadata: bool = False):
    """Resize-to-fit and compress an image. Never upscales."""
    img = _open_image(input_file)
    if max_width or max_height:
        img.thumbnail((max_width or img.width, max_height or img.height),
                      Image.Resampling.LANCZOS)
    _save_image(img, output_file, quality=quality,
                preserve_metadata=preserve_metadata, optimize=True)

    logger.info("Image optimized", output=output_file)


def adjust_image(input_file: str, output_file: str,
                 brightness: float | None = None, contrast: float | None = None,
                 rotation: float | None = None, flip: str | None = None,
                 fill: str | None = None, preserve_metadata: bool = False):
    """Adjust image brightness, contrast, rotation, or flip.

    `fill` sets the colour of the corners a non-right-angle rotation exposes
    (any PIL colour name or "#rrggbb"). The default is transparent, which the
    previous implementation could not express: it passed no fill at all, so
    `Image.rotate` zero-filled and stamped opaque black wedges into every
    RGB image rotated off-axis, with no way for the caller to override.
    """
    img = _open_image(input_file)
    if brightness is not None:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast is not None:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if rotation is not None:
        if rotation % 90:
            # Off-axis: promote to RGBA so exposed corners can be transparent
            # (or a chosen colour) rather than silently black.
            img = img.convert("RGBA") if img.mode != "RGBA" else img
            fill_colour = fill if fill is not None else (0, 0, 0, 0)
            img = img.rotate(-rotation, expand=True, fillcolor=fill_colour)
        else:
            img = img.rotate(-rotation, expand=True)  # negative for CW rotation
    if flip == "horizontal":
        img = ImageOps.mirror(img)
    elif flip == "vertical":
        img = ImageOps.flip(img)
    elif flip is not None:
        raise ValueError(f"Invalid flip: '{flip}'. Use 'horizontal' or 'vertical'.")
    _save_image(img, output_file, preserve_metadata=preserve_metadata)

    logger.info("Image adjusted", output=output_file)


def get_image_info(input_file: str) -> dict:
    """Return image metadata as a dict.

    Dimensions are as *displayed*: a portrait phone photo stores a landscape
    buffer plus an orientation tag, and reporting the raw buffer mislabels every
    such image. `raw_width`/`raw_height` expose the stored values when they
    differ.
    """
    with Image.open(input_file) as handle:
        raw_size = handle.size
        oriented = ImageOps.exif_transpose(handle)
        size = oriented.size if oriented is not None else raw_size
        info = {
            "width": size[0],
            "height": size[1],
            "format": handle.format,
            "mode": handle.mode,
            "file_size": os.path.getsize(input_file),
        }
        if size != raw_size:
            info["raw_width"], info["raw_height"] = raw_size
        return info


def describe_color_profile(input_file: str) -> str | None:
    """Return the embedded ICC profile's description, if any."""
    with Image.open(input_file) as img:
        profile = img.info.get("icc_profile")
    if not profile:
        return None
    import io
    return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(profile))).strip()

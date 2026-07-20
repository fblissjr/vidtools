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

from contextlib import contextmanager
from PIL import Image, UnidentifiedImageError, ImageCms, ImageEnhance, ImageOps

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

#: Colour modes each container can actually store. A mode outside this set is
#: converted before saving rather than raising `OSError: cannot write mode
#: CMYK as PNG` -- which is what a scanned CMYK TIFF used to do.
_FORMAT_MODES = {
    ".png": {"1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA"},
    ".jpg": {"L", "RGB", "CMYK"},
    ".jpeg": {"L", "RGB", "CMYK"},
    ".webp": {"RGB", "RGBA"},
    ".avif": {"RGB", "RGBA"},
    ".bmp": {"1", "L", "P", "RGB"},
    ".gif": {"L", "P"},
    # TIFF stores essentially anything; omitted deliberately so it is unrestricted.
}

#: Container formats where extra frames really are animation. MPO and
#: multi-page TIFF also report `is_animated`, but their extra frames are gain
#: maps and pages -- taking the first is correct there.
_ANIMATED_FORMATS = {"GIF", "WEBP", "PNG", "APNG"}

#: Modes PIL's high-quality resampling filters cannot process. Resizing an
#: I;16 scan raised "image has wrong mode" from LANCZOS, not from the encoder.
_RESAMPLE_UNSUPPORTED = {"1", "I;16", "I;16B", "I;16L", "P"}


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _supports_alpha(supported: set[str] | None) -> bool:
    """Whether a container can store an alpha channel at all."""
    return supported is None or bool(supported & {"RGBA", "LA", "PA"})


def _prepare_for_resample(img: Image.Image) -> Image.Image:
    """Promote modes that resample badly to a continuous-tone mode first.

    Pillow silently downgrades to NEAREST for `1` and `P` modes regardless of
    the filter requested, so a palette image asked for LANCZOS quietly gets
    nearest-neighbour instead -- aliased output, not the requested quality.
    Promoting first means the filter actually applies.

    (Pillow 11.0 added filter support for `I;16*`, so the 16-bit promotion is
    belt-and-braces for older builds rather than a hard requirement here.)
    """
    if img.mode not in _RESAMPLE_UNSUPPORTED:
        return img
    if img.mode == "P":
        return img.convert("RGBA" if "transparency" in img.info else "RGB")
    if img.mode == "1":
        return img.convert("L")
    return img.convert("I")  # 16-bit integer modes


@contextmanager
def _open_raw(input_file: str):
    """`Image.open` with hostile-input errors translated to `ValueError`.

    Raw: no orientation applied, no ICC handling. Use `_open_image` for anything
    that renders pixels. This exists for the three call sites that legitimately
    need the undecoded original -- `strip_image_metadata` edits the container
    rather than re-rendering, and the two inspectors must not transpose what
    they are reporting on -- but which still have to fail cleanly.

    Pillow's own exceptions are not caught by the CLI entrypoint, so without
    this they reach the user as a traceback that also prints absolute paths.
    """
    try:
        with Image.open(input_file) as handle:
            yield handle
    except Image.DecompressionBombError as exc:
        raise ValueError(
            f"{input_file}: image is too large to process safely ({exc}). "
            "This is the guard against decompression-bomb files."
        ) from exc
    except UnidentifiedImageError as exc:
        raise ValueError(
            f"{input_file}: not a readable image. The extension may not match "
            "the actual file contents."
        ) from exc
    except OSError as exc:
        raise ValueError(f"{input_file}: could not be read as an image ({exc}).") from exc


def _open_image(input_file: str) -> Image.Image:
    """Open an image with orientation applied and the colour profile retained.

    `Image.open` does not apply EXIF Orientation, so a phone photo arrives as a
    landscape buffer with a tag saying "display me rotated". Any geometry op
    then works on the wrong pixels -- `crop_image` cropped a region the user
    could not see. Applying it here makes the buffer match what the user was
    looking at, and lets the tag be dropped safely afterwards.

    `exif_transpose` clears the tag it consumes, so the caller must not
    re-attach the source EXIF wholesale.

    Malformed and hostile input is converted to `ValueError` here. Pillow's own
    exceptions (`DecompressionBombError`, `UnidentifiedImageError`) are not
    caught by the CLI entrypoint, so they reached the user as a traceback --
    which is both unreadable and prints absolute paths. A decompression bomb is
    a small file that decodes huge: 18000x18000 of one colour is under 1 MB on
    disk and about 1 GB in memory. Pillow refuses those by default; this only
    changes how the refusal is reported.
    """
    with _open_raw(input_file) as handle:
        if getattr(handle, "is_animated", False) and handle.format in _ANIMATED_FORMATS:
            # Silently keeping frame 1 discarded the rest: a real 239-frame,
            # 20 MB animated WebP came out as a single 52 KB still. `.gif` is
            # already routed to the video path for this reason; `.webp` and
            # `.png` are not, so guard here rather than by extension.
            #
            # Keyed to format as well as `is_animated`: MPO (iPhone HDR)
            # reports is_animated=True, but its extra frame is a gain map, not
            # animation, and taking frame 1 there is correct. Multi-page TIFF
            # is likewise excluded.
            raise ValueError(
                f"'{os.path.basename(input_file)}' is animated "
                f"({handle.n_frames} frames). The image ops would keep only the "
                "first frame and silently discard the rest. Use the video verbs "
                "(e.g. `vidtools convert`) on an animated file, or extract a "
                "still first with `vidtools screenshot`."
            )
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
    supported = _FORMAT_MODES.get(ext)

    # 1. Alpha first. This must precede the generic mode conversion below: a bare
    #    `.convert("RGB")` *discards* the alpha channel and keeps whatever RGB
    #    values sat underneath, which in real transparent PNGs is (0,0,0) -- so a
    #    logo on a transparent field came out on a black field rather than white.
    has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    if has_alpha and not _supports_alpha(supported):
        rgba = img.convert("RGBA")
        backdrop = Image.new("RGBA", img.size, (255, 255, 255, 255))
        backdrop.paste(rgba, mask=rgba.split()[-1])
        # Always land on RGB here and let step 2 pick the container's mode.
        # Choosing the final mode inline repeated the L-before-P mistake and
        # desaturated GIF output a second way.
        img = backdrop.convert("RGB")

    # 2. Whatever mode remains, make it one the container can store.
    if supported is not None and img.mode not in supported:
        if img.mode in ("I", "I;16", "I;16B", "I;16L", "F") and "L" in supported:
            img = img.convert("I").point(lambda v: v * (1 / 257)).convert("L")
        elif "RGB" in supported:
            img = img.convert("RGB")
        elif "P" in supported:
            # Before "L": palette keeps colour, greyscale throws it away. GIF
            # declares {"L", "P"} and no "RGB", so an L-first order silently
            # desaturated every colour image saved as .gif.
            img = img.convert("P", palette=Image.Palette.ADAPTIVE)
        elif "L" in supported:
            img = img.convert("L")

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

    # Pillow's JPEG encoder reads `comment` from im.info when the caller does not
    # supply one, so a source COM marker rides straight past the EXIF allowlist.
    # It is free-form text -- exactly the payload class that turned up as 109 KB
    # of generation prompt in video comment tags -- so clear it explicitly.
    if not preserve_metadata and ext in _JPEG_EXTS:
        save_kwargs.setdefault("comment", b"")

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
        # Clamp to 1px: scaling a very small image down rounds to zero, and PIL
        # rejects a zero dimension. Found on a real 1x1 image at 25%.
        new_w = max(1, int(img.width * percentage))
        new_h = max(1, int(img.height * percentage))
    elif width:
        new_w = width
        new_h = max(1, int(img.height * (width / img.width)))
    elif height:
        new_h = height
        new_w = max(1, int(img.width * (height / img.height)))
    else:
        raise ValueError("You must specify a percentage, width, or height.")

    result = _prepare_for_resample(img).resize((new_w, new_h), resampling)
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
    img = _prepare_for_resample(_open_image(input_file))
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    _save_image(img, output_file, preserve_metadata=preserve_metadata)

    logger.info("Thumbnail created", output=output_file, max_size=size)


def strip_image_metadata(input_file: str, output_file: str):
    """Remove EXIF/IPTC/XMP annotations, preserving the image as far as the
    output format allows.

    **This is a re-encode, not a container edit.** For a lossy target that means
    the pixels are not bit-identical: a JPEG decode/encode round-trip is lossy
    even with the source's own quantization tables. What this op guarantees is
    that it does not *degrade* the image -- it does not silently re-quantize to
    a lower quality, and it does not alter colour or geometry.

    Getting that guarantee needs explicit work. Pillow's JPEG encoder defaults
    to quality 75 when the caller omits `quality`, so a naive save halved a
    quality-95 source (119 KB -> 57 KB, 92% of pixels changed) while claiming to
    touch only metadata. Carrying `qtables`/`subsampling` across keeps the size
    and quality of the original.

    Two things are deliberately kept:

    * The **ICC profile** -- dropping it changes how the image renders, which is
      a pixel-level change in all but name.
    * **Display orientation** -- `_open_image` bakes EXIF rotation into the
      raster, so the stored buffer may differ from the source's while the
      rendered result is identical. That is the point: it lets the orientation
      tag be dropped without the image flipping.

    The previous implementation built `Image.new(img.mode, img.size)` and pasted
    into it. For a palette image that allocates a *default* palette, and pasting
    P-into-P copies raw indices without remapping, so a four-colour PNG came out
    solid black.
    """
    ext = _ext(output_file)
    supported = _FORMAT_MODES.get(ext)

    with _open_raw(input_file) as handle:
        source_quantization = getattr(handle, "quantization", None)
        source_subsampling = None
        if handle.format == "JPEG":
            try:
                from PIL import JpegImagePlugin
                source_subsampling = JpegImagePlugin.get_sampling(handle)
            except Exception:  # pragma: no cover - malformed/unusual JPEG
                source_subsampling = None

    img = _open_image(input_file)
    if supported is not None and img.mode not in supported:
        raise ValueError(
            f"Cannot write a {img.mode} image as '{ext}' without converting its "
            f"colour mode, and strip-metadata must not alter the image. Use a "
            f"container that supports {img.mode} (e.g. .tiff), or convert "
            f"explicitly with `vidtools convert` first."
        )

    clean = img.copy()
    profile = img.info.get("icc_profile")
    clean.info = {}
    if img.mode == "P":
        palette = img.getpalette()
        if palette:
            clean.putpalette(palette)
        if "transparency" in img.info:
            clean.info["transparency"] = img.info["transparency"]

    save_kwargs: dict[str, Any] = {}
    if profile:
        save_kwargs["icc_profile"] = profile
    if ext in _JPEG_EXTS and source_quantization:
        # Without these the encoder silently re-quantizes at its default of 75.
        save_kwargs["qtables"] = source_quantization
        if source_subsampling is not None:
            save_kwargs["subsampling"] = source_subsampling
    clean.save(output_file, **save_kwargs)

    logger.info("Image metadata stripped", output=output_file)


def optimize_image(input_file: str, output_file: str, quality: int = 80,
                   max_width: int | None = None, max_height: int | None = None,
                   preserve_metadata: bool = False):
    """Resize-to-fit and compress an image. Never upscales."""
    img = _prepare_for_resample(_open_image(input_file))
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
    with _open_raw(input_file) as handle:
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
    with _open_raw(input_file) as img:
        profile = img.info.get("icc_profile")
    if not profile:
        return None
    import io
    return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(profile))).strip()

"""Contract tests for image metadata and format handling.

These assert what the imaging layer SHOULD do. Every one of them fails against
the current code -- that is the point. They are written before the refactor so
that "the suite is green" means something afterwards.

Each test names the defect it pins and the user-visible consequence, because a
failing assertion on `icc_profile is not None` tells a future reader nothing
about why anyone cared.

Deliberately PIL-only: no ffmpeg, so these must NOT sit behind the integration
tier's ffmpeg skip gate. The existing suite gates its only real image test
behind that skip, which is how six metadata defects survived 304 green tests.
"""
import os

import numpy as np
import pytest
from PIL import Image, ImageOps

from vidtools import image_ops

pytestmark = pytest.mark.pil_real


def _colors(path):
    with Image.open(path) as img:
        return {tuple(c) for c in np.unique(np.asarray(img.convert("RGB")).reshape(-1, 3), axis=0)}


# ---------------------------------------------------------------------------
# 1. EXIF orientation -- the highest-consequence defect
# ---------------------------------------------------------------------------

def test_crop_operates_on_the_displayed_image_not_the_raw_buffer(oriented_jpeg, tmp_path):
    """A user crops what they SEE. PIL crops the raw buffer unless told otherwise.

    Displayed, the photo is 200x400 portrait with the red band across the TOP,
    so crop(0,0,100,100) must return red. Against the raw landscape buffer the
    same rectangle also lands on red -- so this test pins the region by using a
    rectangle that distinguishes them: the displayed BOTTOM-left is black,
    while the raw buffer's equivalent offset is red.
    """
    out = str(tmp_path / "cropped.jpg")
    image_ops.crop_image(oriented_jpeg, out, width=100, height=100, x=0, y=300)
    with Image.open(out) as c:
        center = tuple(int(v) for v in np.asarray(c.convert("RGB"))[50, 50])
    assert center != (255, 0, 0), (
        "crop hit the RAW buffer: y=300 is out of range for the raw 200px-tall "
        "image and valid only for the 400px-tall DISPLAYED image"
    )


def test_ops_preserve_display_orientation(oriented_jpeg, tmp_path):
    """Output must display the same way round as the input did.

    Either bake the rotation into pixels or carry the tag -- but a viewer must
    not see the image flip. Today the tag is dropped and pixels are untouched,
    so every EXIF-aware viewer renders the result sideways.
    """
    with Image.open(oriented_jpeg) as src:
        expected = ImageOps.exif_transpose(src).size

    for name, run in [
        ("resize", lambda o: image_ops.resize_image(oriented_jpeg, o, percentage=1.0)),
        ("convert", lambda o: image_ops.convert_image(oriented_jpeg, o)),
        ("thumbnail", lambda o: image_ops.create_thumbnail(oriented_jpeg, o, size=400)),
    ]:
        out = str(tmp_path / f"{name}.jpg")
        run(out)
        with Image.open(out) as result:
            displayed = ImageOps.exif_transpose(result).size
        assert displayed[0] < displayed[1], (
            f"{name}: input displays portrait {expected}, output displays "
            f"landscape {displayed} -- orientation was lost"
        )


def test_info_reports_displayed_dimensions(oriented_jpeg):
    """get_image_info drives UI. Reporting raw dims mislabels every phone photo."""
    info = image_ops.get_image_info(oriented_jpeg)
    assert info["height"] > info["width"], (
        f"reported {info['width']}x{info['height']} (raw buffer) for an image "
        "that displays as portrait"
    )


# ---------------------------------------------------------------------------
# 2. ICC colour profiles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext", ["jpg", "webp", "png", "avif"])
def test_icc_profile_survives_conversion(wide_gamut_png, tmp_path, ext):
    """Dropping the profile silently reinterprets the image as sRGB.

    PNG and AVIF pass today only because Pillow's plugins fall back to
    im.info; JPEG and WebP read the kwarg only. The op must not depend on
    which plugin happens to be forgiving.
    """
    out = str(tmp_path / f"out.{ext}")
    image_ops.convert_image(wide_gamut_png, out, quality=90)
    with Image.open(out) as result:
        assert result.info.get("icc_profile"), f".{ext} output lost its ICC profile"


# ---------------------------------------------------------------------------
# 3. strip_image_metadata must not touch pixels
# ---------------------------------------------------------------------------

def test_strip_metadata_preserves_palette_colors(palette_png, tmp_path):
    """Stripping METADATA must not alter the IMAGE. Today a palette PNG goes black."""
    out = str(tmp_path / "stripped.png")
    before = _colors(palette_png)
    image_ops.strip_image_metadata(palette_png, out)
    assert _colors(out) == before, "palette indices were re-pointed at a default palette"


def test_strip_metadata_removes_exif(oriented_jpeg, tmp_path):
    """The op's actual job -- pinned so a fix for the above doesn't regress it."""
    out = str(tmp_path / "stripped.jpg")
    image_ops.strip_image_metadata(oriented_jpeg, out)
    with Image.open(out) as result:
        assert not dict(result.getexif()), "EXIF survived strip_image_metadata"


# ---------------------------------------------------------------------------
# 4. Format-specific save options must actually reach the encoder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ext", ["jpg", "webp", "avif"])
def test_quality_parameter_reaches_the_encoder(photo_png, tmp_path, ext):
    """A quality knob that changes nothing is worse than no knob."""
    sizes = {}
    for q in (10, 95):
        out = str(tmp_path / f"q{q}.{ext}")
        image_ops.convert_image(photo_png, out, quality=q)
        sizes[q] = os.path.getsize(out)
    assert sizes[10] < sizes[95], (
        f".{ext}: quality=10 produced {sizes[10]}B and quality=95 produced "
        f"{sizes[95]}B -- the parameter is being dropped"
    )


# ---------------------------------------------------------------------------
# 5. Rotation fill
# ---------------------------------------------------------------------------

def test_non_right_angle_rotation_does_not_bake_black_corners(tmp_path):
    """45deg on an opaque RGB image currently stamps permanent black wedges."""
    src = tmp_path / "flat.png"
    Image.fromarray(np.full((100, 100, 3), 200, np.uint8)).save(src)
    out = str(tmp_path / "rotated.png")
    image_ops.adjust_image(str(src), out, rotation=45)
    with Image.open(out) as result:
        corner = result.convert("RGBA").getpixel((2, 2))
    assert corner[3] == 0 or corner[:3] != (0, 0, 0), (
        f"exposed corner is opaque black {corner}; expected transparent "
        "or a caller-chosen fill"
    )


# ---------------------------------------------------------------------------
# 6. Mode handling must be consistent across ops
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op", ["convert", "resize", "crop", "thumbnail"])
def test_palette_input_to_jpeg_is_handled_uniformly(palette_png, tmp_path, op):
    """convert_image converts P->RGB; its siblings crash on identical input.

    These are the smart-dispatch verbs, so the inconsistency is user-facing.
    """
    out = str(tmp_path / f"{op}.jpg")
    runners = {
        "convert": lambda: image_ops.convert_image(palette_png, out),
        "resize": lambda: image_ops.resize_image(palette_png, out, percentage=0.5),
        "crop": lambda: image_ops.crop_image(palette_png, out, width=4, height=4),
        "thumbnail": lambda: image_ops.create_thumbnail(palette_png, out, size=4),
    }
    runners[op]()
    assert os.path.getsize(out) > 0


# ---------------------------------------------------------------------------
# 7. Metadata policy -- privacy
# ---------------------------------------------------------------------------
# "Metadata" is three different things with three different correct defaults:
#   rendering-critical (Orientation, ICC) -- never optional, handled by baking
#     orientation into pixels and carrying/converting the profile
#   identifying (GPS, Make, Model, DateTimeOriginal) -- stripped by default
#   provenance (Copyright, Artist) -- preserved only on request
#
# The first test below passes today, but only by accident: every op drops
# everything because no save site passes `exif=`. It is pinned here because the
# imaging refactor starts passing `exif=` in order to keep Orientation, and the
# obvious implementation of that would start carrying GPS along with it.

_IDENTIFYING = {0x010F: "Make", 0x0110: "Model", 0x9003: "DateTimeOriginal"}


@pytest.mark.parametrize("op", ["convert", "resize", "thumbnail"])
def test_identifying_metadata_is_stripped_by_default(private_jpeg, tmp_path, op):
    out = str(tmp_path / f"{op}.jpg")
    {
        "convert": lambda: image_ops.convert_image(private_jpeg, out),
        "resize": lambda: image_ops.resize_image(private_jpeg, out, percentage=0.5),
        "thumbnail": lambda: image_ops.create_thumbnail(private_jpeg, out, size=40),
    }[op]()

    with Image.open(out) as result:
        exif = result.getexif()
        leaked = [name for tag, name in _IDENTIFYING.items() if exif.get(tag)]
        assert not exif.get_ifd(0x8825), f"{op} leaked GPS coordinates"
        assert not leaked, f"{op} leaked identifying tags: {leaked}"


def test_provenance_is_preserved_on_request(private_jpeg, tmp_path):
    """`preserve_metadata=True` keeps authorship without re-admitting GPS."""
    out = str(tmp_path / "kept.jpg")
    image_ops.convert_image(private_jpeg, out, preserve_metadata=True)
    with Image.open(out) as result:
        exif = result.getexif()
        assert exif.get(0x8298) == "(c) Example", "copyright was dropped"
        assert not exif.get_ifd(0x8825), (
            "preserve_metadata must not re-admit GPS -- provenance and location "
            "are different categories"
        )


# ---------------------------------------------------------------------------
# 8. Degenerate sizes -- found on real files, not imagined
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dims", [(1, 1), (1, 400), (3, 2)])
def test_percentage_resize_never_produces_a_zero_dimension(tmp_path, dims):
    """Scaling a tiny image rounds to zero, and PIL rejects a zero dimension.

    A real 1x1 image in the wild hit this at --percentage 0.25.
    """
    src = tmp_path / "tiny.png"
    Image.new("RGB", dims, (10, 20, 30)).save(src)
    out = str(tmp_path / "out.png")
    image_ops.resize_image(str(src), out, percentage=0.25)
    with Image.open(out) as result:
        assert result.width >= 1 and result.height >= 1

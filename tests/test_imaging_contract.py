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


# ---------------------------------------------------------------------------
# 9. Regressions found by review of this work
# ---------------------------------------------------------------------------

def test_colour_survives_gif_output(tmp_path):
    """GIF declares {"L", "P"} and no "RGB", so an L-before-P fallback order
    silently desaturated every colour image saved as .gif."""
    arr = np.zeros((60, 90, 3), np.uint8)
    arr[:, :30], arr[:, 30:60], arr[:, 60:] = (255, 0, 0), (0, 255, 0), (0, 0, 255)
    src = tmp_path / "c.png"
    Image.fromarray(arr).save(src)
    out = str(tmp_path / "c.gif")
    image_ops.convert_image(str(src), out)
    with Image.open(out) as result:
        colours = {tuple(c) for c in
                   np.unique(np.asarray(result.convert("RGB")).reshape(-1, 3), axis=0)}
    assert not all(r == g == b for r, g, b in colours), f"gif came out greyscale: {colours}"


@pytest.mark.parametrize("ext", ["jpg", "bmp", "gif"])
def test_transparency_flattens_onto_white_not_black(tmp_path, ext):
    """Real transparent PNGs store (0,0,0) under alpha=0, so a bare
    `convert("RGB")` puts the subject on a black field. The composite step
    existed but was dead code -- the mode-conversion block ran first."""
    rgba = np.zeros((40, 40, 4), np.uint8)
    rgba[10:30, 10:30] = (30, 144, 255, 255)
    src = tmp_path / "logo.png"
    Image.fromarray(rgba, "RGBA").save(src)
    out = str(tmp_path / f"logo.{ext}")
    image_ops.convert_image(str(src), out)
    with Image.open(out) as result:
        field = result.convert("RGB").getpixel((1, 1))
    assert min(field) > 200, f".{ext} transparent field came out {field}, expected near-white"


def test_jpeg_comment_marker_does_not_leak(tmp_path):
    """Pillow's JPEG encoder falls back to im.info["comment"], which bypasses
    the EXIF allowlist entirely. Free-form text is exactly the payload class
    that showed up as 109 KB of generation prompt in video comment tags."""
    src = tmp_path / "c.jpg"
    Image.fromarray(np.full((40, 40, 3), 120, np.uint8)).save(src, comment=b"a secret prompt")
    out = str(tmp_path / "out.jpg")
    image_ops.convert_image(str(src), out)
    with Image.open(out) as result:
        assert not result.info.get("comment"), "JPEG comment marker survived the strip"


def test_strip_metadata_does_not_requantize_jpeg(tmp_path):
    """Pillow defaults to quality=75 when none is given, so a naive save halved
    a quality-95 source while claiming to touch only metadata. A JPEG round-trip
    is never bit-exact, but it must not *degrade*."""
    rng = np.random.default_rng(0)
    base = Image.fromarray(rng.integers(0, 256, (64, 64, 3), dtype=np.uint8))
    src = tmp_path / "hq.jpg"
    base.resize((512, 512), Image.Resampling.LANCZOS).save(src, quality=95)
    out = str(tmp_path / "stripped.jpg")
    image_ops.strip_image_metadata(str(src), out)

    before, after = os.path.getsize(src), os.path.getsize(out)
    assert after > before * 0.9, (
        f"stripping metadata shrank a quality-95 JPEG from {before} to {after} bytes -- "
        "the encoder re-quantized at its default quality"
    )


# --- G5: the extension tables must not drift apart ------------------------
#
# `_FORMAT_MODES` (image_ops) says what we can *write*; `IMAGE_EXTS` (utils)
# says what the CLI dispatches as an image, i.e. what we can *read*. They are
# separate tables serving different questions, and they drifted: `compress
# --allow-avif` wrote a .avif that `vidtools info` then refused to open. A tool
# that emits a file it cannot read back is the bug worth pinning.
#
# `.gif` is the one deliberate exception -- a still-encoder target that
# `_detect_kind` routes to video, since an animated GIF is a video to every
# other verb.

WRITE_ONLY_NOT_IMAGE_INPUT = {".gif"}


def test_every_writable_format_is_readable_back():
    from vidtools.image_ops import _FORMAT_MODES
    from vidtools.utils import IMAGE_EXTS
    writable = set(_FORMAT_MODES) - WRITE_ONLY_NOT_IMAGE_INPUT
    unreadable = writable - IMAGE_EXTS
    assert not unreadable, (
        f"can write {sorted(unreadable)} but the CLI will not dispatch them as "
        "images -- the tool would emit a file it cannot open"
    )


def test_gif_stays_video_for_dispatch():
    """Guards the exception above: .gif is writable as a still but must keep
    routing to video, or every video verb loses GIF support."""
    from vidtools.cli import _detect_kind
    from vidtools.utils import IMAGE_EXTS
    assert _detect_kind("x.gif") == "video"
    assert ".gif" not in IMAGE_EXTS


# --- G13: hostile and malformed input -------------------------------------
#
# A decompression bomb is a small file that decodes huge (18000x18000 solid
# colour is under 1 MB on disk and ~1 GB in RAM). Pillow already refuses these,
# but it raises PIL-specific exceptions that main() does not catch, so the user
# got a traceback -- which also prints absolute paths. The op must fail with a
# clean, actionable message like every other validation error.

@pytest.mark.pil_real
def test_decompression_bomb_raises_clean_error(tmp_path):
    from vidtools.image_ops import _open_image
    bomb = tmp_path / "bomb.png"
    Image.new("RGB", (18000, 18000), (255, 0, 0)).save(bomb, optimize=True)
    with pytest.raises(ValueError) as exc:
        _open_image(str(bomb))
    msg = str(exc.value).lower()
    assert "too large" in msg or "pixel" in msg
    assert "traceback" not in msg


@pytest.mark.pil_real
def test_non_image_raises_clean_error(tmp_path):
    from vidtools.image_ops import _open_image
    fake = tmp_path / "notreally.png"
    fake.write_bytes(b"this is not a png at all")
    with pytest.raises(ValueError) as exc:
        _open_image(str(fake))
    assert "not a readable image" in str(exc.value).lower()


@pytest.mark.pil_real
def test_truncated_image_raises_clean_error(tmp_path):
    from vidtools.image_ops import _open_image
    good = tmp_path / "good.png"
    Image.new("RGB", (400, 400), (10, 200, 30)).save(good)
    data = good.read_bytes()
    trunc = tmp_path / "trunc.png"
    trunc.write_bytes(data[:len(data) // 3])
    with pytest.raises(ValueError):
        _open_image(str(trunc))


@pytest.mark.pil_real
@pytest.mark.parametrize("op", ["info", "strip_metadata", "profile"])
def test_inspectors_also_fail_cleanly_on_hostile_input(tmp_path, op):
    """The inspectors and strip_image_metadata bypass _open_image on purpose --
    they must not transpose what they report on -- but they share the error
    translation, so hostile input still raises ValueError rather than a PIL
    exception the CLI does not catch."""
    from vidtools import image_ops
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"definitely not a png")
    out = tmp_path / "out.png"
    fn = {
        "info": lambda: image_ops.get_image_info(str(bad)),
        "strip_metadata": lambda: image_ops.strip_image_metadata(str(bad), str(out)),
        "profile": lambda: image_ops.describe_color_profile(str(bad)),
    }[op]
    with pytest.raises(ValueError):
        fn()

"""Shared test fixtures and collection rules.

Two things here are load-bearing, and both exist because their absence let six
image-metadata defects survive a fully green suite:

1. **Marker-based skipping.** Previously `test_integration.py` carried a
   module-level `skipif` on ffmpeg, so a machine without ffmpeg ran 289 of 304
   tests, skipped 15, and exited 0 — reporting success while dropping every
   behavioural check. Worse, two of those 15 needed only PIL. Skipping is now
   driven by the `ffmpeg` marker, so PIL-only behaviour tests always run.

2. **Fixtures that can actually detect a bug.** The old shared image fixture was
   a single flat colour, which makes crop/flip/rotate correctness untestable in
   principle — a real horizontal flip and a no-op are pixel-identical on it.
   `sample_image` is now four distinct quadrants.
"""
import shutil

import numpy as np
import pytest
from PIL import Image, ImageCms

from vidtools import progress as progress_mod


# ---------------------------------------------------------------------------
# collection: skip on capability, not on file location
# ---------------------------------------------------------------------------

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def pytest_collection_modifyitems(config, items):
    """Skip only `ffmpeg`-marked tests when the toolchain is absent.

    `pil_real` tests exercise the real PIL encode/decode path and need no
    external binary, so they must never be gated behind ffmpeg.
    """
    if HAS_FFMPEG:
        return
    skip = pytest.mark.skip(reason="requires ffmpeg/ffprobe on PATH")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolate_progress_sink():
    """Clear the thread-local progress sink around every test, suite-wide.

    This guard previously lived only in test_progress.py, so isolation depended
    on which file a leak happened to originate in.
    """
    progress_mod.set_progress_sink(None)
    yield
    progress_mod.set_progress_sink(None)


# ---------------------------------------------------------------------------
# image fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def quadrant_array():
    """200x100 RGB in four distinct quadrants.

    Distinct quadrants are what make geometry assertions possible: with a flat
    fill, a flip, a 180-degree rotation and a no-op all produce identical bytes.
    """
    arr = np.zeros((100, 200, 3), np.uint8)
    arr[:50, :100] = (255, 0, 0)      # top-left     red
    arr[:50, 100:] = (0, 255, 0)      # top-right    green
    arr[50:, :100] = (0, 0, 255)      # bottom-left  blue
    arr[50:, 100:] = (255, 255, 0)    # bottom-right yellow
    return arr


@pytest.fixture
def sample_image(tmp_path, quadrant_array):
    """The standard image input: 200x100, four quadrants, no metadata."""
    path = tmp_path / "sample.png"
    Image.fromarray(quadrant_array).save(path)
    return str(path)


@pytest.fixture
def palette_png(tmp_path):
    """P-mode image with a known 3-colour palette.

    Palette mode is where `strip_image_metadata` destroys the image and where
    resize/crop/thumbnail crash on a JPEG target.
    """
    img = Image.fromarray(np.tile(np.arange(8, dtype=np.uint8)[:, None], (1, 8))).convert("P")
    img.putpalette([255, 0, 0, 0, 255, 0, 0, 0, 255] + [0, 0, 0] * 253)
    path = tmp_path / "palette.png"
    img.save(path)
    return str(path)


@pytest.fixture
def oriented_jpeg(tmp_path):
    """A landscape pixel buffer tagged Orientation=6 — what a phone writes.

    Displayed correctly this is 200x400 portrait. The red band sits on the RAW
    left edge, which after display rotation runs along the top.
    """
    arr = np.zeros((200, 400, 3), np.uint8)
    arr[:, :100] = (255, 0, 0)
    exif = Image.Exif()
    exif[0x0112] = 6
    path = tmp_path / "oriented.jpg"
    Image.fromarray(arr).save(path, exif=exif)
    return str(path)


@pytest.fixture
def private_jpeg(tmp_path):
    """An image carrying the tags a real camera writes, including GPS.

    Used to assert the metadata policy: identifying tags are stripped by
    default, rendering-critical data is not.
    """
    exif = Image.Exif()
    exif[0x0112] = 1                       # Orientation (upright)
    exif[0x010F] = "ExampleCorp"           # Make
    exif[0x0110] = "Model X"               # Model
    exif[0x8298] = "(c) Example"           # Copyright
    exif[0x9003] = "2026:01:01 12:00:00"   # DateTimeOriginal
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2] = "N", (37.0, 46.0, 0.0)
    gps[3], gps[4] = "W", (122.0, 25.0, 0.0)
    path = tmp_path / "private.jpg"
    Image.fromarray(np.full((80, 120, 3), 90, np.uint8)).save(path, exif=exif)
    return str(path)


@pytest.fixture
def icc_bytes():
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


@pytest.fixture
def wide_gamut_png(tmp_path, icc_bytes):
    """An image with an attached ICC profile.

    Dropping the profile silently reinterprets the pixels as sRGB.
    """
    path = tmp_path / "tagged.png"
    Image.fromarray(np.full((64, 64, 3), 128, np.uint8)).save(path, icc_profile=icc_bytes)
    return str(path)


@pytest.fixture
def photo_png(tmp_path):
    """Continuous-tone content — compresses like a photograph, not line art."""
    rng = np.random.default_rng(0)
    small = Image.fromarray(rng.integers(0, 256, (64, 64, 3), dtype=np.uint8))
    path = tmp_path / "photo.png"
    small.resize((256, 256), Image.Resampling.LANCZOS).save(path)
    return str(path)


# ---------------------------------------------------------------------------
# ffmpeg command capture
# ---------------------------------------------------------------------------

@pytest.fixture
def capture_ffmpeg(monkeypatch):
    """Capture the ffmpeg command built by a video op without running it.

    Replaces five near-identical copies of this fixture that were defined
    per-test-class in test_operations.py.
    """
    calls = []
    monkeypatch.setattr("vidtools.video_ops.run_ffmpeg", lambda cmd: calls.append(cmd))
    return calls

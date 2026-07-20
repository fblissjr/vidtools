"""Tests for image operations."""
import os
from PIL import Image
import pytest
from vidtools.image_ops import (
    resize_image, convert_image, crop_image, create_thumbnail,
    strip_image_metadata, optimize_image, adjust_image, get_image_info,
)


@pytest.fixture
def sample_image_with_exif(tmp_path):
    """Create a JPEG with basic EXIF data."""
    img = Image.new("RGB", (200, 100), color=(0, 255, 0))
    path = str(tmp_path / "test_exif.jpg")
    # JPEG format can contain EXIF
    img.save(path, exif=img.getexif().tobytes())
    return path


class TestResizeImage:
    def test_resize_by_percentage(self, sample_image, tmp_path):
        out = str(tmp_path / "resized.png")
        resize_image(sample_image, out, percentage=0.5)
        with Image.open(out) as img:
            assert img.size == (100, 50)

    def test_resize_by_width(self, sample_image, tmp_path):
        out = str(tmp_path / "resized.png")
        resize_image(sample_image, out, width=100)
        with Image.open(out) as img:
            assert img.width == 100
            assert img.height == 50  # aspect ratio preserved

    def test_resize_by_height(self, sample_image, tmp_path):
        out = str(tmp_path / "resized.png")
        resize_image(sample_image, out, height=50)
        with Image.open(out) as img:
            assert img.height == 50
            assert img.width == 100

    def test_resize_no_params_raises(self, sample_image, tmp_path):
        out = str(tmp_path / "resized.png")
        with pytest.raises(ValueError):
            resize_image(sample_image, out)

    def test_resize_no_progress_callback_param(self):
        # Image ops are synchronous - they don't use progress callbacks
        import inspect
        sig = inspect.signature(resize_image)
        assert "progress_callback" not in sig.parameters


class TestConvertImage:
    def test_png_to_jpg(self, sample_image, tmp_path):
        out = str(tmp_path / "converted.jpg")
        convert_image(sample_image, out)
        with Image.open(out) as img:
            assert img.format == "JPEG"

    def test_png_to_webp(self, sample_image, tmp_path):
        out = str(tmp_path / "converted.webp")
        convert_image(sample_image, out)
        with Image.open(out) as img:
            assert img.format == "WEBP"

    def test_quality_parameter(self, sample_image, tmp_path):
        out_low = str(tmp_path / "low.jpg")
        out_high = str(tmp_path / "high.jpg")
        convert_image(sample_image, out_low, quality=10)
        convert_image(sample_image, out_high, quality=95)
        assert os.path.getsize(out_low) < os.path.getsize(out_high)


class TestCropImage:
    def test_crop_basic(self, sample_image, tmp_path):
        out = str(tmp_path / "cropped.png")
        crop_image(sample_image, out, width=50, height=50, x=10, y=10)
        with Image.open(out) as img:
            assert img.size == (50, 50)

    def test_crop_out_of_bounds_raises(self, sample_image, tmp_path):
        out = str(tmp_path / "cropped.png")
        with pytest.raises(ValueError):
            crop_image(sample_image, out, width=300, height=50, x=0, y=0)


class TestCreateThumbnail:
    def test_thumbnail_preserves_aspect(self, sample_image, tmp_path):
        out = str(tmp_path / "thumb.png")
        create_thumbnail(sample_image, out, size=50)
        with Image.open(out) as img:
            assert max(img.size) <= 50
            # 200x100 scaled to fit 50x50 -> 50x25
            assert img.size == (50, 25)


class TestStripMetadata:
    def test_strip_removes_exif(self, sample_image_with_exif, tmp_path):
        out = str(tmp_path / "stripped.jpg")
        strip_image_metadata(sample_image_with_exif, out)
        with Image.open(out) as img:
            exif = img.getexif()
            assert len(exif) == 0


class TestOptimizeImage:
    def test_optimize_reduces_size(self, sample_image, tmp_path):
        # Create a larger image for more obvious size difference
        big_img = Image.new("RGB", (1000, 1000), color=(128, 128, 128))
        big_path = str(tmp_path / "big.png")
        big_img.save(big_path)
        out = str(tmp_path / "optimized.jpg")
        optimize_image(big_path, out, quality=50, max_width=500)
        with Image.open(out) as img:
            assert img.width <= 500

    def test_optimize_respects_max_dimensions(self, sample_image, tmp_path):
        out = str(tmp_path / "optimized.png")
        optimize_image(sample_image, out, max_width=50, max_height=50)
        with Image.open(out) as img:
            assert img.width <= 50
            assert img.height <= 50


class TestAdjustImage:
    def test_rotate(self, sample_image, tmp_path):
        out = str(tmp_path / "rotated.png")
        adjust_image(sample_image, out, rotation=90)
        with Image.open(out) as img:
            # 200x100 rotated 90 -> 100x200
            assert img.size == (100, 200)

    def test_flip_horizontal(self, sample_image, tmp_path):
        """Assert the pixels actually mirror, not just that the size survived.

        The previous version checked only `img.size == (200, 100)`. A flip
        preserves dimensions -- and so does doing nothing at all, so a no-op
        implementation passed. `sample_image` is four distinct quadrants
        precisely so this is testable; on a flat fill a real flip and a no-op
        are pixel-identical and the property is untestable in principle.
        """
        import numpy as np
        out = str(tmp_path / "flipped.png")
        adjust_image(sample_image, out, flip="horizontal")
        with Image.open(sample_image) as src, Image.open(out) as img:
            assert img.size == src.size
            before, after = np.array(src), np.array(img)
            assert np.array_equal(after, before[:, ::-1]), "not mirrored on x"
            assert not np.array_equal(after, before), "flip was a no-op"


    def test_flip_vertical(self, sample_image, tmp_path):
        import numpy as np
        out = str(tmp_path / "flipped_v.png")
        adjust_image(sample_image, out, flip="vertical")
        with Image.open(sample_image) as src, Image.open(out) as img:
            before, after = np.array(src), np.array(img)
            assert np.array_equal(after, before[::-1, :]), "not mirrored on y"
            assert not np.array_equal(after, before), "flip was a no-op"


class TestGetImageInfo:
    def test_returns_correct_info(self, sample_image):
        info = get_image_info(sample_image)
        assert info["width"] == 200
        assert info["height"] == 100
        assert info["format"] == "PNG"
        assert info["mode"] == "RGB"
        assert info["file_size"] > 0

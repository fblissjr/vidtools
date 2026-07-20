"""CLI tests that drive the image path only.

Separate from test_integration.py because these need PIL and nothing else.
Living in that file meant inheriting its module-level `ffmpeg` marker, so the
project's only real image tests vanished on any machine without ffmpeg -- which
is a large part of why six image-metadata defects survived a green suite.
"""
import os

import pytest

pytestmark = pytest.mark.pil_real


class TestCliErrorHandling:
    """main() should turn operation ValueErrors into a clean exit, not a traceback.

    Image-path only, so these carry `pil_real` and run without ffmpeg.
    """

    def test_bad_rotation_exits_cleanly(self, sample_image, tmp_path, monkeypatch):
        import sys

        from vidtools import cli
        out = str(tmp_path / "o.png")
        monkeypatch.setattr(sys, "argv", ["vidtools", "rotate", "-r", "abc", sample_image, out])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert not os.path.exists(out)

    def test_image_rotate_accepts_float_angle(self, sample_image, tmp_path):
        """A 45-degree rotation must actually rotate: expand=True grows the canvas."""
        from vidtools.cli import parse_arguments
        out = str(tmp_path / "r.png")
        args = parse_arguments(["rotate", "-r", "45.5", sample_image, out])
        args.func(args)
        from PIL import Image
        with Image.open(sample_image) as src:
            before = src.size
        with Image.open(out) as im:
            assert im.size[0] > before[0] and im.size[1] > before[1], (
                f"45deg rotation of {before} produced {im.size}; expected a larger "
                "canvas. A no-op would keep the original size."
            )

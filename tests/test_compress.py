"""Tests for the smart-compression engine.

These are shape/logic tests on synthetic PIL images -- cheap, no ffmpeg, no
external encoders. Behavior that depends on what a real encoder does to a real
photograph lives in tests/test_integration.py instead.
"""
import io

import numpy as np
import pytest
from PIL import Image

from vidtools.compress import (
    Candidate,
    _classify_content,
    _pick_probe_crop,
    available_formats,
    compress_image,
    compute_ssim,
    recommend,
)

# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------

def _photographic(size=(256, 256), seed=0):
    """Continuous-tone noise -- stands in for a photo/upscaled render."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)
    # Smooth it slightly so it is compressible rather than pure white noise.
    img = Image.fromarray(base).resize((size[0] // 4, size[1] // 4), Image.Resampling.LANCZOS)
    return img.resize(size, Image.Resampling.LANCZOS)


def _flat(size=(256, 256)):
    """Few-color synthetic graphic -- stands in for a screenshot / line art."""
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    arr[:, : size[0] // 2] = (255, 0, 0)
    arr[:, size[0] // 2 :] = (0, 0, 255)
    arr[: size[1] // 8, :] = (255, 255, 255)
    return Image.fromarray(arr)


@pytest.fixture
def photo_png(tmp_path):
    p = tmp_path / "photo.png"
    _photographic().save(p)
    return str(p)


@pytest.fixture
def flat_png(tmp_path):
    p = tmp_path / "flat.png"
    _flat().save(p)
    return str(p)


@pytest.fixture
def alpha_png(tmp_path):
    p = tmp_path / "alpha.png"
    img = _photographic().convert("RGBA")
    arr = np.array(img)
    arr[:, :, 3] = 128
    Image.fromarray(arr).save(p)
    return str(p)


# --------------------------------------------------------------------------
# SSIM
# --------------------------------------------------------------------------

def test_ssim_identical_is_one():
    img = _photographic()
    assert compute_ssim(img, img) == pytest.approx(1.0, abs=1e-6)


def test_ssim_degrades_monotonically_with_quality(tmp_path):
    """A worse encode must score lower. This is the property the search relies on."""
    img = _photographic()
    scores = []
    for q in (95, 60, 20):
        p = tmp_path / f"q{q}.jpg"
        img.save(p, quality=q)
        with Image.open(p) as decoded:
            scores.append(compute_ssim(img, decoded.convert("RGB")))
    assert scores == sorted(scores, reverse=True), scores
    assert scores[-1] < 0.99


def test_ssim_is_bounded():
    a, b = _photographic(seed=1), _photographic(seed=2)
    score = compute_ssim(a, b)
    assert -1.0 <= score <= 1.0
    assert score < 0.5, "unrelated images should score poorly"


def test_ssim_banding_matches_single_pass():
    """Banded processing exists to bound memory; it must not change the result."""
    a = _photographic(size=(512, 512), seed=3)
    b = a.copy().resize((256, 256)).resize((512, 512))
    assert compute_ssim(a, b, band_rows=64) == pytest.approx(
        compute_ssim(a, b, band_rows=10_000), abs=1e-6
    )


def test_ssim_rejects_mismatched_sizes():
    with pytest.raises(ValueError, match="same size"):
        compute_ssim(_photographic((64, 64)), _photographic((32, 32)))


# --------------------------------------------------------------------------
# content analysis
# --------------------------------------------------------------------------

def test_classify_flat_vs_photographic(flat_png, photo_png):
    with Image.open(flat_png) as img:
        assert _classify_content(img) == "flat"
    with Image.open(photo_png) as img:
        assert _classify_content(img) == "photographic"


def test_smooth_gradient_is_photographic_not_flat():
    """Regression: upscaled art has many flat runs but is not flat content.

    A real 6144x2560 upscaled render measured 69% adjacent-equal pixels with
    ~29k unique colours. Treating flat runs alone as "flat" misclassified it
    and made the search spend ~35s on full-res lossless encodes that never win.
    """
    # Low-slope 2-D gradients: under one grey level per pixel, so neighbours
    # quantise to the same value, while the 2-D variation still yields tens of
    # thousands of distinct colours. This reproduces the real signature
    # (~91% adjacent-equal, ~24k colours) without shipping a large fixture.
    y, x = np.mgrid[0:512, 0:512].astype(np.float64)
    img = Image.fromarray(
        np.dstack([x * 0.25, y * 0.25, (x + y) * 0.125]).clip(0, 255).astype(np.uint8)
    )

    probe = img.copy()
    probe.thumbnail((512, 512), Image.Resampling.NEAREST)
    luma = np.asarray(probe.convert("L"), dtype=np.float64)
    n_colors = len(np.unique(np.asarray(probe.convert("RGB")).reshape(-1, 3), axis=0))
    # Guard the fixture itself: it must have BOTH properties, or it isn't
    # exercising the distinction between flat runs and a small palette.
    assert float(np.mean(np.diff(luma, axis=1) == 0)) >= 0.5, "fixture lost its flat runs"
    assert n_colors > 4096, "fixture palette too small to test the distinction"

    assert _classify_content(img) == "photographic"


def test_probe_crop_prefers_the_busiest_region():
    """Tuning on a flat region would over-recommend aggressive quality."""
    arr = np.zeros((512, 512, 3), dtype=np.uint8)
    arr[:, :] = (30, 30, 30)  # mostly flat
    rng = np.random.default_rng(7)
    arr[300:400, 300:400] = rng.integers(0, 256, (100, 100, 3), dtype=np.uint8)
    crop = _pick_probe_crop(Image.fromarray(arr), size=128)
    assert np.array(crop).std() > 40, "crop landed on a flat region"


def test_probe_crop_smaller_than_target_returns_whole_image():
    img = _photographic((64, 64))
    assert _pick_probe_crop(img, size=256).size == (64, 64)


# --------------------------------------------------------------------------
# recommendation
# --------------------------------------------------------------------------

def test_recommend_ranks_by_size_and_meets_target(photo_png):
    report = recommend(photo_png, target_ssim=0.95)
    assert report.candidates, "no candidates produced"
    sizes = [c.size for c in report.candidates]
    assert sizes == sorted(sizes), "candidates must be ranked smallest-first"
    assert report.winner.ssim >= 0.95
    assert report.winner is report.candidates[0]


def test_recommend_lossless_candidates_are_pixel_identical(photo_png):
    """Decode each candidate and compare pixels, rather than trusting the field.

    `Candidate.ssim` is hardcoded to 1.0 for lossless candidates -- it is never
    measured -- so asserting on it checks a constant in the source. This would
    keep passing if an encode silently became lossy; comparing decoded pixels
    against the source will not.
    """
    report = recommend(photo_png, lossless=True)
    assert report.candidates
    assert all(c.lossless for c in report.candidates)

    with Image.open(photo_png) as src:
        expected = np.asarray(src.convert("RGB"))
    for candidate in report.candidates:
        assert candidate.data is not None
        with Image.open(io.BytesIO(candidate.data)) as decoded:
            actual = np.asarray(decoded.convert("RGB"))
        assert np.array_equal(actual, expected), (
            f"{candidate.fmt} claims lossless but decoded pixels differ from the source"
        )


def test_recommend_never_proposes_jpeg_for_alpha(alpha_png):
    report = recommend(alpha_png, target_ssim=0.9)
    assert report.has_alpha
    assert "jpeg" not in {c.fmt for c in report.candidates}


def test_unsupported_output_extension_is_rejected(photo_png, tmp_path):
    """An extension compress cannot encode must error, not get a second suffix.

    `.bmp`/`.gif`/`.tif` are all in this project's own IMAGE_EXTS, so silently
    writing `photo.bmp.jpg` was a plausible thing for a user to hit.
    """
    with pytest.raises(ValueError, match=r"Cannot compress to '\.bmp'"):
        compress_image(photo_png, str(tmp_path / "photo.bmp"), target_ssim=0.9)


def test_explicit_avif_extension_implies_opt_in(photo_png, tmp_path):
    """Naming an .avif output is an explicit request; --allow-avif guards accidents."""
    if "avif" not in available_formats():
        pytest.skip("Pillow build has no AVIF support")
    out = tmp_path / "explicit.avif"
    report = compress_image(photo_png, str(out), target_ssim=0.9)
    assert out.exists() and report.winner.fmt == "avif"


def test_recommend_excludes_avif_by_default(photo_png):
    """AVIF compresses best but no major vision API decodes it, so it is opt-in.

    The Claude API accepts JPEG/PNG/GIF/WebP; Gemini accepts PNG/JPEG/WebP/
    HEIC/HEIF. Neither converts client-side, so defaulting to AVIF would
    produce files those tools cannot read at all.
    """
    report = recommend(photo_png, target_ssim=0.9)
    assert "avif" not in {c.fmt for c in report.candidates}


def test_recommend_includes_avif_when_opted_in(photo_png):
    if "avif" not in available_formats():
        pytest.skip("Pillow build has no AVIF support")
    report = recommend(photo_png, target_ssim=0.9, allow_avif=True)
    assert "avif" in {c.fmt for c in report.candidates}


def test_default_candidates_are_all_vision_api_safe(photo_png, alpha_png):
    """Every default-mode candidate must be decodable by Claude and Gemini."""
    universally_readable = {"png", "jpeg", "webp"}
    for path in (photo_png, alpha_png):
        report = recommend(path, target_ssim=0.9)
        assert {c.fmt for c in report.candidates} <= universally_readable


def test_recommend_respects_max_size(photo_png):
    report = recommend(photo_png, max_size=8_000)
    assert report.winner.size <= 8_000


def test_recommend_raises_when_no_candidate_meets_constraints(photo_png):
    with pytest.raises(ValueError, match="No candidate"):
        recommend(photo_png, max_size=10)


def test_recommend_skips_unavailable_formats(photo_png, monkeypatch):
    """A Pillow build without AVIF must degrade, not crash."""
    monkeypatch.setattr("vidtools.compress.available_formats", lambda: {"webp", "jpeg", "png"})
    report = recommend(photo_png, target_ssim=0.9)
    assert "avif" not in {c.fmt for c in report.candidates}
    assert report.candidates


# --------------------------------------------------------------------------
# end-to-end op
# --------------------------------------------------------------------------

def test_compress_image_writes_winner_with_correct_extension(photo_png, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report = compress_image(photo_png, str(out_dir / "result"), target_ssim=0.95)
    written = list(out_dir.iterdir())
    assert len(written) == 1
    assert written[0].suffix.lstrip(".") == report.winner.fmt.replace("jpeg", "jpg")
    assert written[0].stat().st_size == report.winner.size


def test_compress_image_honors_explicit_output_extension(photo_png, tmp_path):
    out = tmp_path / "forced.webp"
    report = compress_image(photo_png, str(out), target_ssim=0.9)
    assert out.exists()
    assert report.winner.fmt == "webp"


def test_compress_image_dry_run_writes_nothing(photo_png, tmp_path):
    out = tmp_path / "nope.webp"
    report = compress_image(photo_png, str(out), target_ssim=0.9, dry_run=True)
    assert not out.exists()
    assert report.winner is not None


def test_compress_image_reports_source_size(photo_png):
    import os

    report = recommend(photo_png, target_ssim=0.9)
    assert report.source_size == os.path.getsize(photo_png)


def test_candidate_ratio_helper():
    c = Candidate(fmt="avif", quality=70, lossless=False, size=250, ssim=0.99)
    assert c.ratio(1000) == pytest.approx(4.0)


def test_available_formats_includes_baseline():
    fmts = available_formats()
    assert {"png", "jpeg"} <= fmts


def test_probe_crop_cannot_veto_an_achievable_target(tmp_path):
    """A hard patch must not block an encode the whole image can reach.

    `_pick_probe_crop` deliberately selects the busiest tile, so the probe
    scores *lower* than the image as a whole. When the probe alone decided,
    a small noisy region vetoed encodes that were comfortably achievable --
    7 of 18 real photographs failed outright with "No candidate satisfied
    the constraints".

    This fixture is that shape: a smooth, highly-compressible field with one
    small patch of incompressible noise.
    """
    # Larger than PROBE_SIZE, or _pick_probe_crop returns the whole image and
    # the probe/whole distinction this test depends on does not exist.
    rng = np.random.default_rng(11)
    y, x = np.mgrid[0:1024, 0:1024].astype(np.float64)
    arr = np.dstack([x * 0.2, y * 0.2, (x + y) * 0.1]).clip(0, 255).astype(np.uint8)
    arr[400:700, 400:700] = rng.integers(0, 256, (300, 300, 3), dtype=np.uint8)
    src = tmp_path / "mixed.png"
    Image.fromarray(arr).save(src)

    # The probe must genuinely be the hard region, or the test proves nothing.
    from vidtools.compress import _encode, _pick_probe_crop, _score
    with Image.open(src) as opened:
        whole = opened.convert("RGB")
    crop = _pick_probe_crop(whole)
    probe_score = _score(crop, _encode(crop, "webp", 80, lossless=False))
    whole_score = _score(whole, _encode(whole, "webp", 80, lossless=False))
    assert probe_score < whole_score, (
        f"fixture invalid: probe {probe_score:.4f} should be harder than "
        f"the whole image {whole_score:.4f}"
    )

    report = recommend(str(src), target_ssim=0.98)
    assert report.winner.ssim >= 0.98


def test_unreachable_target_reports_what_was_achievable(tmp_path):
    """Failure should name a target that would work, not just say no."""
    rng = np.random.default_rng(5)
    noise = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    src = tmp_path / "noise.png"
    Image.fromarray(noise).save(src)
    with pytest.raises(ValueError, match=r"best achievable was SSIM 0\.\d+"):
        recommend(str(src), target_ssim=0.9999)

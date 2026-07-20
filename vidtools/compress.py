"""Smart image compression: measure candidate encodes, then pick a winner.

Why this is a module and not another op in `image_ops.py`: the other image ops
are one-shot PIL calls. This one is a search -- it encodes an image several
times at several qualities, scores each against the original, and only then
decides what to write. `image_ops.py` stays PIL-simple; this holds the engine.

Why it measures instead of guessing: a heuristic ("AI art -> AVIF q80") is a
guess dressed up as a rule, and the answer genuinely depends on the image.
Measuring size-against-SSIM across a candidate set is what actually produces a
defensible recommendation, and it costs a few seconds.

Two deliberate limitations, stated plainly:

1. SSIM is a mediocre perceptual metric. It is used here because it is
   computable with numpy alone and is monotonic in encode quality, which is all
   the search requires. It is *not* a claim about perceived quality --
   SSIMULACRA2 or butteraugli would be materially better and neither is a
   dependency. Treat the default target as eyeball-calibrated, not principled.
2. Format compatibility cannot be inferred from pixels, so AVIF is **opt-in**
   despite usually winning on ratio. The deciding constraint is that no major
   vision API accepts it: the Claude API takes JPEG/PNG/GIF/WebP and the
   Gemini API takes PNG/JPEG/WebP/HEIC/HEIF -- neither accepts AVIF, and
   neither converts client-side, so an AVIF is not a valid image input at
   all. WebP is the intersection of Claude, Gemini, and browsers, which makes
   it the safe default. Pass `allow_avif=True` when the destination is
   storage or a browser rather than a model.

Related sizing note for model-bound images: Claude downscales to a 2576px long
edge (1568px outside the high-resolution tier), so pixels beyond that are
discarded on arrival. Compressing a 6144px-wide image without resizing it
optimizes bytes the model will never see.

Encoding is Pillow-only -- no cwebp/avifenc/oxipng subprocesses -- so this adds
no runtime dependency beyond what the project already installs. A Pillow build
without AVIF or WebP degrades to the formats it does have (see
`available_formats`); it must never crash, which is the same lesson the
`screenshot` op learned from a build missing libwebp.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import numpy as np
import structlog
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image, features

from .progress import report_progress

logger = structlog.get_logger(__name__)

# SSIM constants (Wang et al. 2004), for 8-bit dynamic range.
_WINDOW = 11
_SIGMA = 1.5
_L = 255.0
_C1 = (0.01 * _L) ** 2
_C2 = (0.03 * _L) ** 2

# Quality search bounds. Below ~25 every codec is visibly broken; above ~95
# file size explodes for no measurable SSIM gain.
_Q_MIN = 25
_Q_MAX = 95

#: Side length of the native-resolution probe crop used for the quality search.
PROBE_SIZE = 512

#: Default fidelity target. Eyeball-calibrated -- see module docstring.
DEFAULT_TARGET_SSIM = 0.98

_EXT_FOR_FMT = {"jpeg": "jpg", "webp": "webp", "avif": "avif", "png": "png"}
_FMT_FOR_EXT = {
    "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp", "avif": "avif", "png": "png",
}


# ---------------------------------------------------------------------------
# capability detection
# ---------------------------------------------------------------------------

def available_formats() -> set[str]:
    """Formats this Pillow build can actually encode.

    PNG and JPEG are baseline. WebP and AVIF depend on how Pillow was compiled,
    so they are probed rather than assumed.
    """
    fmts = {"png", "jpeg"}
    for name, flag in (("webp", "webp"), ("avif", "avif")):
        try:
            if features.check(flag):
                fmts.add(name)
        except Exception:  # pragma: no cover - defensive, older Pillow
            pass
    return fmts


# ---------------------------------------------------------------------------
# SSIM
# ---------------------------------------------------------------------------

def _gaussian_kernel(size: int = _WINDOW, sigma: float = _SIGMA) -> np.ndarray:
    ax = np.arange(size, dtype=np.float64) - (size - 1) / 2.0
    k = np.exp(-(ax**2) / (2.0 * sigma**2))
    return k / k.sum()


def _blur(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Separable gaussian blur, 'valid' mode. Output is (H-k+1, W-k+1)."""
    horiz = sliding_window_view(x, kernel.size, axis=1) @ kernel
    return sliding_window_view(horiz, kernel.size, axis=0) @ kernel


def _ssim_sums(x: np.ndarray, y: np.ndarray, kernel: np.ndarray) -> tuple[float, int]:
    """Return (sum of SSIM map, number of windows) for one band."""
    mu_x, mu_y = _blur(x, kernel), _blur(y, kernel)
    mu_x_sq, mu_y_sq, mu_xy = mu_x * mu_x, mu_y * mu_y, mu_x * mu_y
    sigma_x = _blur(x * x, kernel) - mu_x_sq
    sigma_y = _blur(y * y, kernel) - mu_y_sq
    sigma_xy = _blur(x * y, kernel) - mu_xy

    numerator = (2 * mu_xy + _C1) * (2 * sigma_xy + _C2)
    denominator = (mu_x_sq + mu_y_sq + _C1) * (sigma_x + sigma_y + _C2)
    ssim_map = numerator / denominator
    return float(ssim_map.sum()), int(ssim_map.size)


def _luma(img: Image.Image) -> np.ndarray:
    """Luma plane as float64. Alpha is ignored -- SSIM here scores colour only."""
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img, dtype=np.float64)


def compute_ssim(a: Image.Image, b: Image.Image, band_rows: int = 512) -> float:
    """Mean SSIM between two same-size images, computed on luma.

    Processed in horizontal bands because the sliding-window view materialises
    an (H, W, 11) array -- at 6144x2560 that is gigabytes in one pass. Bands
    overlap by `_WINDOW - 1` rows so the result is *identical* to a single
    pass, not an approximation.
    """
    if a.size != b.size:
        raise ValueError(f"Images must be the same size to compare: {a.size} vs {b.size}")

    x, y = _luma(a), _luma(b)
    height = x.shape[0]
    if height < _WINDOW or x.shape[1] < _WINDOW:
        raise ValueError(f"Images must be at least {_WINDOW}x{_WINDOW} to compare")

    kernel = _gaussian_kernel()
    n_out = height - _WINDOW + 1
    band_rows = max(1, band_rows)

    total, count = 0.0, 0
    for start in range(0, n_out, band_rows):
        stop = min(n_out, start + band_rows)
        band_slice = slice(start, stop + _WINDOW - 1)
        band_total, band_count = _ssim_sums(x[band_slice], y[band_slice], kernel)
        total += band_total
        count += band_count

    return total / count if count else 1.0


# ---------------------------------------------------------------------------
# content analysis
# ---------------------------------------------------------------------------

def _classify_content(img: Image.Image) -> str:
    """Classify as 'flat' (screenshots, line art, UI) or 'photographic'.

    Flat content wants lossless or near-lossless -- lossy codecs put ringing on
    hard edges and text, which SSIM under-penalises. Photographic content is
    where lossy pays off enormously.
    """
    probe = img.copy()
    probe.thumbnail((512, 512), Image.Resampling.NEAREST)
    luma = _luma(probe)

    # Fraction of horizontally-adjacent pixel pairs that are exactly equal.
    # Synthetic graphics are full of flat runs; photographs essentially never are.
    if luma.shape[1] < 2:
        return "flat"
    flat_fraction = float(np.mean(np.diff(luma, axis=1) == 0))

    pixels = np.asarray(probe.convert("RGB")).reshape(-1, 3)
    unique_colors = len(np.unique(pixels, axis=0))

    # A small palette is decisive on its own. Flat *runs* are not: upscaled and
    # heavily-denoised photos are full of adjacent-equal pixels simply because
    # smooth gradients quantize to the same 8-bit value -- a real upscaled
    # render measured 69% adjacent-equal with 29k unique colours. Flat runs
    # therefore only count as evidence when the palette is also small.
    if unique_colors <= 256:
        return "flat"
    if flat_fraction >= 0.5 and unique_colors <= 4096:
        return "flat"
    return "photographic"


def _pick_probe_crop(img: Image.Image, size: int = PROBE_SIZE) -> Image.Image:
    """Crop the highest-variance `size`x`size` tile, at native resolution.

    Two decisions worth defending:

    * Native resolution, not a downscaled proxy. Downscaling averages away the
      exact blocking and ringing the search is trying to detect, so a proxy
      systematically over-recommends aggressive quality.
    * Highest variance, not the centre. Tuning quality on a flat sky region
      produces a setting that falls apart on the detailed part of the image.
      Picking the busiest tile tunes to the worst case.
    """
    if img.width <= size or img.height <= size:
        return img.convert(img.mode)

    luma = _luma(img)
    best_score, best_xy = -1.0, (0, 0)
    step = max(size // 2, 1)
    for top in range(0, img.height - size + 1, step):
        for left in range(0, img.width - size + 1, step):
            score = float(luma[top : top + size, left : left + size].std())
            if score > best_score:
                best_score, best_xy = score, (left, top)

    left, top = best_xy
    return img.crop((left, top, left + size, top + size))


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------

def _encode(img: Image.Image, fmt: str, quality: int | None, lossless: bool) -> bytes:
    """Encode to an in-memory buffer. Returns the encoded bytes."""
    save_kwargs: dict[str, object] = {}
    target = img

    if fmt == "jpeg":
        if target.mode != "RGB":
            target = target.convert("RGB")
        save_kwargs.update(quality=quality, optimize=True, progressive=True)
    elif fmt == "webp":
        if lossless:
            save_kwargs.update(lossless=True, quality=100, method=6)
        else:
            save_kwargs.update(quality=quality, method=6)
    elif fmt == "avif":
        save_kwargs.update(quality=quality, speed=6)
    elif fmt == "png":
        save_kwargs.update(optimize=True)
    else:  # pragma: no cover - guarded by caller
        raise ValueError(f"Unsupported format: {fmt}")

    buffer = io.BytesIO()
    target.save(buffer, format=fmt.upper(), **save_kwargs)
    return buffer.getvalue()


def _decode(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as img:
        return img.convert("RGB")


def _score(original: Image.Image, data: bytes, band_rows: int = 512) -> float:
    return compute_ssim(original.convert("RGB"), _decode(data), band_rows=band_rows)


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One encode of the source image, with its measured cost and fidelity."""

    fmt: str
    quality: int | None
    lossless: bool
    size: int
    ssim: float
    data: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def extension(self) -> str:
        return _EXT_FOR_FMT[self.fmt]

    def ratio(self, source_size: int) -> float:
        """How many times smaller than the source. 4.0 means a 75% reduction."""
        return source_size / self.size if self.size else float("inf")

    def label(self) -> str:
        return f"{self.fmt}{'' if self.lossless else f' q{self.quality}'}" + (
            " (lossless)" if self.lossless else ""
        )


@dataclass(frozen=True)
class CompressionReport:
    """Everything the search learned, so the recommendation can be audited."""

    source_path: str
    source_size: int
    width: int
    height: int
    has_alpha: bool
    content_class: str
    candidates: list[Candidate]
    winner: Candidate
    target_ssim: float | None
    max_size: int | None
    output_path: str | None = None


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------

def _shortlist(has_alpha: bool, content_class: str, lossless: bool,
               allow_avif: bool, formats: set[str]) -> tuple[list[str], list[str]]:
    """Return (lossy_formats, lossless_formats) worth trying."""
    lossless_fmts = [f for f in ("webp", "png") if f in formats]
    if lossless:
        return [], lossless_fmts

    lossy = []
    for fmt in ("avif", "webp", "jpeg"):
        if fmt not in formats:
            continue
        if fmt == "avif" and not allow_avif:
            # Excluded by default: no major vision API decodes AVIF, so it is
            # opt-in rather than a default win. See the module docstring.
            continue
        if fmt == "jpeg" and has_alpha:
            continue  # JPEG cannot carry an alpha channel
        lossy.append(fmt)

    # Flat art (text, UI, line work) frequently beats its own lossy encodes on
    # size *and* is exactly lossless, so it always competes losslessly too.
    return lossy, lossless_fmts if content_class == "flat" else []


def _search_quality(probe: Image.Image, fmt: str, target_ssim: float) -> int | None:
    """Lowest quality whose probe SSIM still meets the target, or None."""
    lo, hi, best = _Q_MIN, _Q_MAX, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if _score(probe, _encode(probe, fmt, mid, lossless=False), band_rows=512) >= target_ssim:
            best, hi = mid, mid - 1
        else:
            lo = mid + 1
    return best


def _search_size(img: Image.Image, fmt: str, max_size: int) -> int | None:
    """Highest quality whose full-size encode fits the byte budget, or None."""
    lo, hi, best = _Q_MIN, _Q_MAX, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if len(_encode(img, fmt, mid, lossless=False)) <= max_size:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def recommend(
    input_file: str,
    *,
    target_ssim: float = DEFAULT_TARGET_SSIM,
    lossless: bool = False,
    max_size: int | None = None,
    allow_avif: bool = False,
    formats: list[str] | None = None,
) -> CompressionReport:
    """Search candidate encodings and return a ranked, measured report.

    Objective depends on the constraint given:

    * `lossless=True` -- only exact encodes; smallest wins.
    * `max_size` -- maximise fidelity subject to the byte budget.
    * otherwise -- minimise size subject to `target_ssim`.

    `allow_avif` opts into AVIF, which usually compresses best but is not
    accepted by the Claude or Gemini vision APIs -- see the module docstring.

    Raises ValueError if nothing satisfies the constraints.
    """
    with Image.open(input_file) as opened:
        img = opened.convert("RGBA" if opened.mode in ("RGBA", "LA", "PA") else "RGB")
        has_alpha = img.mode == "RGBA"

    source_size = os.path.getsize(input_file)
    content_class = _classify_content(img)
    probe = _pick_probe_crop(img)

    usable = available_formats()
    if formats is not None:
        usable = usable & set(formats)
    lossy_fmts, lossless_fmts = _shortlist(has_alpha, content_class, lossless, allow_avif, usable)

    logger.info("Compression search started", input=input_file,
                content=content_class, alpha=has_alpha,
                lossy=lossy_fmts, lossless=lossless_fmts)

    candidates: list[Candidate] = []
    total_steps = max(len(lossy_fmts) + len(lossless_fmts), 1)
    step = 0

    for fmt in lossless_fmts:
        data = _encode(img, fmt, None, lossless=True)
        candidates.append(Candidate(fmt=fmt, quality=None, lossless=True,
                                    size=len(data), ssim=1.0, data=data))
        step += 1
        report_progress(int(step / total_steps * 100), f"probed {fmt} lossless")

    for fmt in lossy_fmts:
        # Quality is searched on the probe crop (fast); the winning setting is
        # then encoded at full resolution and re-scored, because crop SSIM only
        # ranks settings -- it is not the number we report.
        if max_size is not None:
            quality = _search_size(img, fmt, max_size)
        else:
            quality = _search_quality(probe, fmt, target_ssim)

        step += 1
        if quality is None:
            report_progress(int(step / total_steps * 100), f"{fmt}: no setting fits")
            continue

        data = _encode(img, fmt, quality, lossless=False)
        candidates.append(Candidate(fmt=fmt, quality=quality, lossless=False,
                                    size=len(data), ssim=_score(img, data), data=data))
        report_progress(int(step / total_steps * 100), f"probed {fmt} q{quality}")

    # Enforce constraints on the *full-resolution* measurements, not the probe.
    if max_size is not None:
        viable = [c for c in candidates if c.size <= max_size]
    elif lossless:
        viable = candidates
    else:
        viable = [c for c in candidates if c.ssim >= target_ssim]

    if not viable:
        raise ValueError(
            "No candidate satisfied the constraints "
            f"({'max_size=' + str(max_size) if max_size else 'target_ssim=' + str(target_ssim)}). "
            "Loosen the target, or allow more formats."
        )

    viable.sort(key=lambda c: c.size)
    # Size-budget mode maximises fidelity within the budget; the other modes
    # have already filtered on fidelity, so the smallest survivor wins.
    winner = max(viable, key=lambda c: c.ssim) if max_size is not None else viable[0]

    return CompressionReport(
        source_path=input_file,
        source_size=source_size,
        width=img.width,
        height=img.height,
        has_alpha=has_alpha,
        content_class=content_class,
        candidates=viable,
        winner=winner,
        target_ssim=None if (lossless or max_size) else target_ssim,
        max_size=max_size,
    )


def compress_image(
    input_file: str,
    output_file: str | None = None,
    *,
    target_ssim: float = DEFAULT_TARGET_SSIM,
    lossless: bool = False,
    max_size: int | None = None,
    allow_avif: bool = False,
    dry_run: bool = False,
) -> CompressionReport:
    """Compress an image, choosing the format automatically when unconstrained.

    If `output_file` carries a known image extension, that format is forced and
    only its quality is searched. If it has no extension (or is omitted), the
    winning format supplies one.
    """
    forced: list[str] | None = None
    if output_file:
        ext = os.path.splitext(output_file)[1].lstrip(".").lower()
        if ext in _FMT_FOR_EXT:
            forced = [_FMT_FOR_EXT[ext]]

    report = recommend(input_file, target_ssim=target_ssim, lossless=lossless,
                       max_size=max_size, allow_avif=allow_avif, formats=forced)

    if output_file is None:
        base = os.path.splitext(input_file)[0]
        output_file = f"{base}_compressed.{report.winner.extension}"
    elif forced is None:
        output_file = f"{output_file}.{report.winner.extension}"

    if dry_run:
        logger.info("Compression dry run", input=input_file, winner=report.winner.label())
        return report

    assert report.winner.data is not None
    with open(output_file, "wb") as handle:
        handle.write(report.winner.data)

    logger.info("Image compressed", output=output_file, format=report.winner.fmt,
                size=report.winner.size, ssim=round(report.winner.ssim, 6))
    report_progress(100, "compression complete")

    return CompressionReport(**{**report.__dict__, "output_path": output_file})

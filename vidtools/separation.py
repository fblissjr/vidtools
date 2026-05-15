"""Audio source separation (vocals/instrumental/stems).

Wraps the `audio-separator` library behind a backend-swappable interface so a
future MLX-native implementation can drop in without breaking the CLI surface
or job dispatch. The current backend uses audio-separator's curated registry
(see `audio-separator --list_models` for the full list).

Optional extra: install with `uv sync --extra separation`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .progress import report_progress


class SeparationBackend(Protocol):
    """A backend produces one output file per stem the model declares."""

    def separate(self, input_file: str, model: str, output_dir: str) -> list[str]: ...

    def available_models(self) -> list[str]: ...


class AudioSeparatorBackend:
    """Wraps `audio_separator.separator.Separator`. Uses its built-in registry.

    Models are auto-downloaded to audio-separator's default cache on first use.
    """

    def __init__(self):
        try:
            from audio_separator.separator import Separator
        except ImportError as e:
            raise ImportError(
                "Audio source separation requires the 'separation' extra. "
                "Install with: uv sync --extra separation"
            ) from e
        self._Separator = Separator
        self._sep = None
        self._cached: tuple[str, str] | None = None

    def separate(self, input_file: str, model: str, output_dir: str) -> list[str]:
        # Cache by (model, output_dir): audio-separator takes output_dir at
        # construction time, so a different dir requires a fresh instance --
        # but reusing the same instance avoids reloading model weights from disk
        # on back-to-back calls with the same model+dir.
        key = (model, output_dir)
        if self._sep is None or self._cached != key:
            report_progress(5, f"Loading model: {model}")
            self._sep = self._Separator(output_dir=output_dir)
            self._sep.load_model(model_filename=model)
            self._cached = key
        report_progress(30, "Running separation")
        outputs = self._sep.separate(input_file)
        report_progress(100, "Done")
        return [str(Path(output_dir) / p) if not Path(p).is_absolute() else p
                for p in outputs]

    def available_models(self) -> list[str]:
        sep = self._Separator()
        listing = sep.list_supported_model_files()
        # audio-separator returns a nested dict {arch: {filename: metadata}}.
        # Flatten to a list of filenames.
        names: list[str] = []
        if isinstance(listing, dict):
            for arch_models in listing.values():
                if isinstance(arch_models, dict):
                    names.extend(arch_models.keys())
        return sorted(names)


_DEFAULT_BACKEND: SeparationBackend | None = None


def get_backend() -> SeparationBackend:
    """Return the active separation backend, instantiating it on first call."""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = AudioSeparatorBackend()
    return _DEFAULT_BACKEND


def separate_audio(input_file: str, model: str, output_dir: str) -> list[str]:
    """Separate `input_file` into stems using `model`.

    Returns a list of output file paths (one per stem). Stem names come from
    the model's metadata; filenames follow the backend's convention.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    return get_backend().separate(input_file, model, output_dir)


def list_models() -> list[str]:
    """Return all model filenames supported by the active backend."""
    return get_backend().available_models()

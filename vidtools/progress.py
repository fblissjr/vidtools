"""Thread-local progress sink. Operations call report_progress() without
knowing or caring about who's consuming the events.

The CLI sets a tqdm-based sink. Operations stay free of progress callbacks
in their signatures.

Pattern is the same as Python's logging module or Flask's `g`: ambient
context carried through thread-local storage.
"""

import threading
from contextlib import contextmanager
from typing import Callable

ProgressSink = Callable[[int, str], None]

_local = threading.local()


def set_progress_sink(sink: ProgressSink | None) -> None:
    """Install a progress sink for the current thread (or clear with None)."""
    _local.sink = sink


def get_progress_sink() -> ProgressSink | None:
    """Return the current thread's progress sink, or None if none set."""
    return getattr(_local, 'sink', None)


def report_progress(percent: int, message: str = "") -> None:
    """Report progress to the current thread's sink. No-op if no sink is set."""
    sink = getattr(_local, 'sink', None)
    if sink is not None:
        sink(percent, message)


@contextmanager
def progress_context(sink: ProgressSink):
    """Set a sink for the duration of the with-block, then clear it.

    Guarantees cleanup even on exception.
    """
    set_progress_sink(sink)
    try:
        yield
    finally:
        set_progress_sink(None)


class FfmpegError(RuntimeError):
    """Raised when an ffmpeg subprocess returns a non-zero exit code."""
    pass

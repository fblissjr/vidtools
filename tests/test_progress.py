"""Tests for the thread-local progress sink."""
import threading
import pytest
from vidtools.progress import (
    set_progress_sink, report_progress, get_progress_sink,
    progress_context, FfmpegError,
)


@pytest.fixture(autouse=True)
def _clear_sink():
    """Ensure no sink leaks between tests."""
    set_progress_sink(None)
    yield
    set_progress_sink(None)


class TestProgressSink:
    def test_no_sink_is_noop(self):
        # Should not raise even with no sink configured
        report_progress(50, "halfway")

    def test_sink_receives_events(self):
        events = []
        set_progress_sink(lambda p, m: events.append((p, m)))
        report_progress(25, "quarter")
        report_progress(50, "half")
        assert events == [(25, "quarter"), (50, "half")]

    def test_sink_can_be_cleared(self):
        events = []
        set_progress_sink(lambda p, m: events.append((p, m)))
        report_progress(10, "first")
        set_progress_sink(None)
        report_progress(20, "second")
        assert events == [(10, "first")]

    def test_default_message_is_empty(self):
        events = []
        set_progress_sink(lambda p, m: events.append((p, m)))
        report_progress(75)
        assert events == [(75, "")]

    def test_get_progress_sink(self):
        assert get_progress_sink() is None
        sink = lambda p, m: None
        set_progress_sink(sink)
        assert get_progress_sink() is sink


class TestProgressContext:
    def test_sets_and_clears(self):
        events = []
        sink = lambda p, m: events.append((p, m))
        with progress_context(sink):
            report_progress(50, "in")
            assert get_progress_sink() is sink
        # After exit, sink is cleared
        assert get_progress_sink() is None
        report_progress(60, "out")
        assert events == [(50, "in")]

    def test_clears_on_exception(self):
        with pytest.raises(ValueError):
            with progress_context(lambda p, m: None):
                raise ValueError("boom")
        assert get_progress_sink() is None


class TestThreadIsolation:
    def test_sinks_are_thread_local(self):
        main_events = []
        thread_events = []
        set_progress_sink(lambda p, m: main_events.append((p, m)))

        def worker():
            # Thread sees no sink initially
            assert get_progress_sink() is None
            set_progress_sink(lambda p, m: thread_events.append((p, m)))
            report_progress(99, "from_thread")

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        report_progress(1, "from_main")
        assert main_events == [(1, "from_main")]
        assert thread_events == [(99, "from_thread")]


class TestFfmpegError:
    def test_is_runtime_error(self):
        err = FfmpegError("test")
        assert isinstance(err, RuntimeError)
        assert str(err) == "test"

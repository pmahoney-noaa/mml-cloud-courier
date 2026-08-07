import pytest
import threading

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.workers import call_async, _guarded


def test_call_async_delivers_the_result_on_the_qt_loop(qtbot, qapp):
    results = []
    bridge = call_async(lambda: 41 + 1, parent=qapp, on_done=results.append)
    qtbot.waitUntil(lambda: results == [42], timeout=5000)


def test_call_async_reports_exceptions_as_text(qtbot, qapp):
    failures = []

    def boom():
        raise RuntimeError("kaput")

    call_async(boom, parent=qapp, on_failed=failures.append)
    qtbot.waitUntil(lambda: failures == ["kaput"], timeout=5000)


def test_guarded_forwards_when_stop_is_not_set():
    stop_event = threading.Event()
    results = []
    wrapped = _guarded(stop_event, results.append)
    wrapped(42)
    assert results == [42]


def test_guarded_suppresses_when_stop_is_set():
    stop_event = threading.Event()
    results = []
    wrapped = _guarded(stop_event, results.append)
    stop_event.set()
    wrapped(42)
    assert results == []

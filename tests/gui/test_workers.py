import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_transfer.gui.workers import call_async


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

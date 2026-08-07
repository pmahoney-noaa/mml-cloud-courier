import pytest

pytest.importorskip("PySide6")

import sys

from mml_cloud_courier.gui.service_control import (
    SERVICE_NAME, start_service_elevated,
)


def test_start_service_uses_runas_sc_start():
    calls = []

    def fake_shell_execute(hwnd, verb, file, params, cwd, show):
        calls.append((verb, file, params))
        return 42

    assert start_service_elevated(shell_execute=fake_shell_execute) is True
    assert calls == [("runas", "sc.exe", f"start {SERVICE_NAME}")]


def test_start_service_reports_refusal():
    assert start_service_elevated(shell_execute=lambda *a: 5) is False

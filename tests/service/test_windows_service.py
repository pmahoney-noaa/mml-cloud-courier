import pytest

win32serviceutil = pytest.importorskip(
    "win32serviceutil", reason="pywin32 is Windows-only"
)


def test_service_class_shape():
    from mml_cloud_courier.service.windows_service import (
        DISPLAY_NAME,
        SERVICE_NAME,
        _build_service_class,
    )

    cls = _build_service_class()
    assert cls._svc_name_ == SERVICE_NAME == "MMLCloudCourier"
    assert cls._svc_display_name_ == DISPLAY_NAME
    assert issubclass(cls, win32serviceutil.ServiceFramework)


def test_service_is_hosted_by_the_venv_python_not_pythonservice():
    """pythonservice.exe cannot host this service: with a per-user Python
    its DLLs are invisible to LocalSystem, and relocated to the venv root
    it gets no site-packages (so `import servicemanager` fails). The
    service must therefore register the venv's own python.exe running this
    module as the SCM binary (Phase 3 gate finding, 2026-08-05)."""
    import sys
    from pathlib import Path

    from mml_cloud_courier.service import windows_service

    cls = windows_service._build_service_class()
    assert cls._exe_name_ == sys.executable
    assert cls._exe_name_.lower().endswith("python.exe")
    module_path = str(Path(windows_service.__file__).resolve())
    assert cls._exe_args_ == f'"{module_path}"'


def test_exe_args_are_none_when_frozen(monkeypatch):
    """Packaged (PyInstaller), the exe IS the service host: ImagePath must
    be the bare exe, no arguments — pywin32 omits them when None."""
    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(windows_service.sys, "frozen", True, raising=False)
    assert windows_service._service_exe_args() is None


def test_exe_args_point_at_the_module_when_not_frozen(monkeypatch):
    from mml_cloud_courier.service import windows_service

    monkeypatch.delattr(windows_service.sys, "frozen", raising=False)
    args = windows_service._service_exe_args()
    assert args.startswith('"') and args.endswith('windows_service.py"')


def test_scm_launch_means_no_arguments(monkeypatch):
    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(windows_service.sys, "argv", ["mmlcc-service.exe"])
    assert windows_service._scm_launch()
    monkeypatch.setattr(
        windows_service.sys, "argv", ["mmlcc-service.exe", "install"]
    )
    assert not windows_service._scm_launch()


def test_run_routes_command_lines_to_main(monkeypatch):
    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(windows_service.sys, "argv", ["mmlcc-service", "--x"])
    calls = []
    monkeypatch.setattr(windows_service, "main", lambda: calls.append(1) or 0)
    assert windows_service.run() == 0
    assert calls


def test_main_propagates_handle_command_line_error(monkeypatch):
    """The installer gates registration success on this exit code — a
    swallowed pywin32 error would show a green installer with no service
    (final-review finding)."""
    import win32serviceutil

    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(
        win32serviceutil, "HandleCommandLine", lambda cls, argv: 1072
    )
    calls = []
    monkeypatch.setattr(
        windows_service, "_configure_restart_on_failure",
        lambda: calls.append(True),
    )
    assert windows_service.main(["install"]) == 1072
    assert not calls  # failure actions never configured on a failed install


def test_main_configures_restart_only_on_successful_install(monkeypatch):
    import win32serviceutil

    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(
        win32serviceutil, "HandleCommandLine", lambda cls, argv: None
    )
    calls = []
    monkeypatch.setattr(
        windows_service, "_configure_restart_on_failure",
        lambda: calls.append(True),
    )
    assert windows_service.main(["install"]) == 0
    assert calls


def test_ensure_service_stdio_replaces_none_streams(monkeypatch):
    """Frozen-service context: an SCM-launched PyInstaller exe has no
    console, so sys.stdout/stderr are None — and uvicorn's default log
    formatter calls sys.stdout.isatty() at Config construction, killing
    SvcDoRun before the host runs (live Phase 6 gate failure: ValueError
    'Unable to configure formatter default', service-specific 0x20000001).
    The venv-hosted python.exe always provided stream objects even without
    a console; _ensure_service_stdio restores that parity."""
    import sys

    from mml_cloud_courier.service import windows_service

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    windows_service._ensure_service_stdio()
    # NUL is a character device, so isatty() is True on Windows — that is
    # fine (colors written to NUL are discarded); what matters is that the
    # streams exist and are probeable.
    assert sys.stdout is not None and sys.stdout.isatty() in (True, False)
    assert sys.stderr is not None
    import uvicorn

    # The exact construction that crashed live: must not raise with the
    # repaired stdio (app import string is never resolved at Config time).
    uvicorn.Config("never.imported:app", host="127.0.0.1", port=59999,
                   log_level="warning")


def test_ensure_service_stdio_leaves_real_streams_alone():
    import sys

    from mml_cloud_courier.service import windows_service

    before_out, before_err = sys.stdout, sys.stderr
    windows_service._ensure_service_stdio()
    assert sys.stdout is before_out
    assert sys.stderr is before_err

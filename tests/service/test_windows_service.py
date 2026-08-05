import pytest

win32serviceutil = pytest.importorskip(
    "win32serviceutil", reason="pywin32 is Windows-only"
)


def test_service_class_shape():
    from mml_cloud_transfer.service.windows_service import (
        DISPLAY_NAME,
        SERVICE_NAME,
        _build_service_class,
    )

    cls = _build_service_class()
    assert cls._svc_name_ == SERVICE_NAME == "MMLCloudTransfer"
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

    from mml_cloud_transfer.service import windows_service

    cls = windows_service._build_service_class()
    assert cls._exe_name_ == sys.executable
    assert cls._exe_name_.lower().endswith("python.exe")
    module_path = str(Path(windows_service.__file__).resolve())
    assert cls._exe_args_ == f'"{module_path}"'

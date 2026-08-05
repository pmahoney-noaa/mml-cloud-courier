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

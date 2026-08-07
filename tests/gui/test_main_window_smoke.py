"""THE main-window smoke test (spec: one pytest-qt smoke, no UI automation).
A real in-process service, a seeded incomplete job with a failed file:
the rail must group it under Needs attention, the tabs must render, and
the Errors tab must show the cause group in taxonomy language."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_transfer.core.errors import ErrorCategory
from mml_cloud_transfer.core.models import Direction, JobStatus, PlannedFile
from mml_cloud_transfer.gui.main_window import MainWindow
from mml_cloud_transfer.gui.session import discover_session
from mml_cloud_transfer.store.db import connect
from mml_cloud_transfer.store.repository import JobRepository


def _seed_incomplete_job(config) -> int:
    conn = connect(config.db_path)
    repo = JobRepository(conn)
    profile_id = repo.get_or_create_profile(bucket="smokebucket", auth_type="adc",
                                            credential_ref=None)
    job_id = repo.create_job(name="smoke", direction=Direction.UPLOAD,
                             source_root="C:\\data", dest_prefix="pre",
                             profile_id=profile_id)
    repo.add_planned_files(job_id, [
        PlannedFile("a.txt", "C:\\data\\a.txt", 10, 1),
        PlannedFile("b.txt", "C:\\data\\b.txt", 20, 1),
    ])
    files = repo.get_files(job_id)
    repo.mark_failed(files[0]["id"], ErrorCategory.PERMISSION_DENIED, "denied")
    repo.set_job_status(job_id, JobStatus.INCOMPLETE)
    conn.close()
    return job_id


@pytest.mark.gui
def test_main_window_renders_a_seeded_job(qtbot, gui_host):
    host, config, token = gui_host
    job_id = _seed_incomplete_job(config)

    window = MainWindow(discover_session(), poll_interval=0.2)
    qtbot.addWidget(window)

    qtbot.waitUntil(lambda: window.rail_job_ids() == [job_id], timeout=10_000)
    window.select_job(job_id)
    qtbot.waitUntil(lambda: window.selected_job_id == job_id, timeout=10_000)

    qtbot.waitUntil(lambda: window.errors_tab.group_count() == 1, timeout=10_000)
    label = window.errors_tab.group_label(0)
    assert "denied" in label.lower()
    assert "1 file" in label

    window.shutdown()

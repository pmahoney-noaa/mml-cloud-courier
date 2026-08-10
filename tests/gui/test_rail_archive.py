"""Show-archived toggle, rail context menu composition, archive handlers.
Fake jobs drive _on_jobs directly; the client fake records archive calls."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.jobs_model import JOB_ID_ROLE, RAIL_GROUPS
from mml_cloud_courier.gui.main_window import MainWindow
from mml_cloud_courier.gui.session import discover_session


@pytest.fixture
def window(qtbot, gui_host):
    win = MainWindow(discover_session(), poll_interval=60)
    qtbot.addWidget(win)
    yield win
    win.shutdown()


def _job(job_id, status="complete", archived=None, profile_id=10):
    return {"id": job_id, "name": f"job-{job_id}", "status": status,
            "direction": "upload", "source_root": "C:\\d", "dest_prefix": "",
            "scheduled_start_at": None,
            "created_at": "2026-08-09T00:00:00+00:00",
            "archived_at": archived, "profile_id": profile_id, "progress": {}}


ARCHIVED_ROW = RAIL_GROUPS.index("archived")


@pytest.mark.gui
def test_archived_group_hidden_until_toggled(qtbot, window):
    window._on_jobs([_job(1)])
    assert window.rail_view.isRowHidden(ARCHIVED_ROW, window.rail_model.invisibleRootItem().index())
    window._set_show_archived(True)
    assert not window.rail_view.isRowHidden(ARCHIVED_ROW, window.rail_model.invisibleRootItem().index())
    assert not window.rail_view.isExpanded(window.rail_model.index(ARCHIVED_ROW, 0))
    window._set_show_archived(False)
    assert window.rail_view.isRowHidden(ARCHIVED_ROW, window.rail_model.invisibleRootItem().index())


@pytest.mark.gui
def test_menu_spec_per_row_kind(qtbot, window):
    window._set_show_archived(True)
    window._on_jobs([_job(1, "complete"), _job(2, "running"),
                     _job(3, "complete", archived="2026-08-09T00:00:00+00:00")])
    def index_for(job_id):
        idx = window._find_rail_index(job_id)
        assert idx is not None
        return idx
    kinds = [k for k, _l, _c in window._rail_menu_spec(index_for(1))]
    assert kinds == ["archive", "toggle_archived"]
    kinds = [k for k, _l, _c in window._rail_menu_spec(index_for(2))]
    assert kinds == ["toggle_archived"]           # running: not archivable
    kinds = [k for k, _l, _c in window._rail_menu_spec(index_for(3))]
    assert kinds == ["unarchive", "toggle_archived"]
    # a group header: toggle only, and its checked flag mirrors the state
    header_spec = window._rail_menu_spec(window.rail_model.index(0, 0))
    assert [k for k, _l, _c in header_spec] == ["toggle_archived"]
    assert header_spec[0][2] is True


@pytest.mark.gui
def test_archive_handler_calls_client_and_clears_selection(qtbot, window, monkeypatch):
    calls = []
    monkeypatch.setattr(window.client, "archive_job",
                        lambda job_id: calls.append(job_id) or {"archived": job_id},
                        raising=False)
    window._on_jobs([_job(1)])
    window.select_job(1)
    qtbot.waitUntil(lambda: window.selected_job_id == 1, timeout=5000)
    window._archive_job(1)
    qtbot.waitUntil(lambda: calls == [1], timeout=5000)
    qtbot.waitUntil(lambda: window.selected_job_id is None, timeout=5000)


@pytest.mark.gui
def test_toggle_off_with_archived_selection_clears_it(qtbot, window):
    window._set_show_archived(True)
    window._on_jobs([_job(1), _job(3, archived="2026-08-09T00:00:00+00:00")])
    window.select_job(3)
    qtbot.waitUntil(lambda: window.selected_job_id == 3, timeout=5000)
    window._set_show_archived(False)
    assert window.selected_job_id is None

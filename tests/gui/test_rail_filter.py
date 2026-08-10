"""Rail profile filter: client-side, jobs already carry profile_id.
The filter bar appears above the rail; polls preserve the filter; the
first-run gate keeps consulting the unfiltered list."""

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.main_window import MainWindow
from mml_cloud_courier.gui.session import discover_session


@pytest.fixture
def window(qtbot, gui_host):
    win = MainWindow(discover_session(), poll_interval=60)
    qtbot.addWidget(win)
    yield win
    win.shutdown()


def _job(job_id, profile_id, status="completed", name=None):
    return {"id": job_id, "name": name or f"job-{job_id}", "status": status,
            "direction": "upload", "profile_id": profile_id, "progress": {}}
    # If sync_rail KeyErrors on a missing field, extend this dict with that
    # field (check _rail_signature in gui/jobs_model.py) — do not weaken the
    # assertions below.


JOBS = [_job(1, 10), _job(2, 10), _job(3, 20)]


@pytest.mark.gui
def test_filter_limits_rail_and_shows_bar(qtbot, window):
    window._on_jobs(JOBS)
    assert sorted(window.rail_job_ids()) == [1, 2, 3]
    window.show_jobs_for_profile(10, "MML imagery")
    assert sorted(window.rail_job_ids()) == [1, 2]
    assert window.filter_bar.isVisibleTo(window)
    assert "MML imagery" in window.filter_label.text()


@pytest.mark.gui
def test_poll_preserves_filter_and_show_all_clears(qtbot, window):
    window._on_jobs(JOBS)
    window.show_jobs_for_profile(20, "PAM archive")
    window._on_jobs(JOBS)                      # next poll tick
    assert window.rail_job_ids() == [3]
    window.show_all_button.click()
    assert sorted(window.rail_job_ids()) == [1, 2, 3]
    assert not window.filter_bar.isVisibleTo(window)


@pytest.mark.gui
def test_filtered_out_selection_clears(qtbot, window):
    window._on_jobs(JOBS)
    window.select_job(3)
    qtbot.waitUntil(lambda: window.selected_job_id == 3, timeout=5000)
    window.show_jobs_for_profile(10, "MML imagery")
    assert window.selected_job_id is None


@pytest.mark.gui
def test_filtered_out_selection_stops_watcher_and_resets_progress_tab(
        qtbot, window, monkeypatch):
    window._on_jobs(JOBS)
    window.select_job(3)
    qtbot.waitUntil(lambda: window.selected_job_id == 3, timeout=5000)
    # Sentinel: only progress_tab.reset() clears this back to "".
    window.progress_tab.headline_name.setText("job-3")
    stop_calls = []
    monkeypatch.setattr(window.watcher, "stop", lambda: stop_calls.append(True))

    window.show_jobs_for_profile(10, "MML imagery")

    assert stop_calls                                     # watcher told to stop
    assert window.progress_tab.headline_name.text() == ""  # tab reset


@pytest.mark.gui
def test_first_run_gate_uses_unfiltered_jobs(qtbot, window):
    window._no_connections = True
    window._on_jobs(JOBS)
    window.show_jobs_for_profile(99, "gone")   # filters out every job
    # jobs exist, so first-run must NOT take over even though the rail is empty
    assert window._content_stack.currentWidget() is not window._first_run


@pytest.mark.gui
def test_submitted_job_hidden_by_filter_clears_the_filter(qtbot, window):
    window._on_jobs(JOBS)                       # profiles 10, 10, 20
    window.show_jobs_for_profile(20, "PAM archive")
    assert window.rail_job_ids() == [3]
    # a poll tick arrives carrying a just-submitted job for another profile
    window._pending_select = 4
    window._on_jobs(JOBS + [_job(4, 10)])
    assert not window.filter_bar.isVisibleTo(window)     # submission wins
    assert sorted(window.rail_job_ids()) == [1, 2, 3, 4]
    qtbot.waitUntil(lambda: window.selected_job_id == 4, timeout=5000)
    assert window._pending_select is None


@pytest.mark.gui
def test_pending_job_not_yet_polled_leaves_filter_alone(qtbot, window):
    window._on_jobs(JOBS)
    window.show_jobs_for_profile(20, "PAM archive")
    window._pending_select = 99                  # poll has not seen it yet
    window._on_jobs(JOBS)
    assert window.filter_bar.isVisibleTo(window)          # filter intact
    assert window._pending_select == 99                   # still pending


@pytest.mark.gui
def test_pending_job_matching_the_filter_keeps_it(qtbot, window):
    window._on_jobs(JOBS)
    window.show_jobs_for_profile(10, "MML imagery")
    window._pending_select = 4
    window._on_jobs(JOBS + [_job(4, 10)])        # profile 10: visible under the filter
    assert window.filter_bar.isVisibleTo(window)          # filter kept
    qtbot.waitUntil(lambda: window.selected_job_id == 4, timeout=5000)
    assert window._pending_select is None

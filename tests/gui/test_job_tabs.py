import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.job_tabs import FilesTab, ProgressTab, SummaryTab


def _summary_tab(qtbot) -> SummaryTab:
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None)
    qtbot.addWidget(tab)
    return tab


def test_summary_tab_report_button_shows_for_a_complete_job(qtbot):
    """The spec's core "morning verdict, then open the report" flow: a
    COMPLETE job must still offer Open report, even though there is
    nothing left to resume."""
    tab = _summary_tab(qtbot)

    tab.update_job({"status": "complete"})
    assert not tab.report_button.isHidden()
    assert tab.resume_button.isHidden()

    tab.update_job({"status": "incomplete"})
    assert not tab.report_button.isHidden()
    assert not tab.resume_button.isHidden()

    tab.update_job({"status": "running"})
    assert tab.report_button.isHidden()
    assert tab.resume_button.isHidden()


def _summary_job(status="incomplete"):
    return {"status": status, "started_at": "2026-08-08T10:00:00+00:00",
            "finished_at": "2026-08-08T11:30:00+00:00",
            "progress": {"files_total": 61022, "bytes_total": 900_000,
                         "bytes_done": 850_000,
                         "state_counts": {"verified": 60599, "failed": 3,
                                          "quarantined": 420}}}


def test_summary_stat_cells(qtbot):
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None)
    qtbot.addWidget(tab)
    tab.update_job(_summary_job())
    assert tab.stat_values["files"].text() == "61,022"
    assert tab.stat_values["transferred"].text() == "850 KB"
    assert tab.stat_values["did_not_transfer"].text() == "423"
    assert tab.stat_values["duration"].text() == "1h 30m"
    assert tab.verdict_tag.isVisibleTo(tab)
    assert tab.footer_label.isVisibleTo(tab)   # quarantined > 0
    assert tab.footer_label.text() == (
        "Excluded files stay recorded in the ledger. The job keeps the"
        " Incomplete verdict until they transfer or you stop retrying them."
    )


def test_summary_tag_and_footer_hidden_when_clean(qtbot):
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None)
    qtbot.addWidget(tab)
    job = _summary_job(status="complete")
    job["progress"]["state_counts"] = {"verified": 61022}
    tab.update_job(job)
    assert not tab.verdict_tag.isVisibleTo(tab)
    assert not tab.footer_label.isVisibleTo(tab)


def test_summary_state_rows_order_and_total_override(qtbot):
    """state_rows_layout must contain one row per nonzero state, ordered
    STATE_ORDER-first then unknown states appended, and each row's bar must
    be handed the job's files_total as an explicit denominator override
    (not the default sum-of-this-row's-single-state, which would always
    read 100%)."""
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None)
    qtbot.addWidget(tab)
    job = _summary_job()
    job["progress"]["files_total"] = 10
    job["progress"]["state_counts"] = {"verified": 2, "failed": 1, "mystery": 1}
    tab.update_job(job)

    assert tab.state_rows_layout.count() == 3

    def _row_parts(index):
        row_widget = tab.state_rows_layout.itemAt(index).widget()
        row_layout = row_widget.layout()
        label = row_layout.itemAt(0).widget()
        bar = row_layout.itemAt(1).widget()
        count_label = row_layout.itemAt(2).widget()
        return label, bar, count_label

    # STATE_ORDER = (..."verified"..."failed"...) so verified sorts before
    # failed; "mystery" is not in STATE_ORDER so it is appended last.
    label0, bar0, count0 = _row_parts(0)
    label1, bar1, count1 = _row_parts(1)
    label2, bar2, count2 = _row_parts(2)
    assert label0.text() == "Verified"
    assert label1.text() == "Failed"
    assert label2.text() == "mystery"   # unknown state: no STATE_LABELS entry, key echoed
    assert count0.text() == "2"
    assert count1.text() == "1"
    assert count2.text() == "1"

    # The bar's private _total is the override denominator passed by
    # SummaryTab (`bar.set_counts({state: count}, total=files_total)`);
    # asserted via the private attribute since _StackedStateBar exposes no
    # public getter for it.
    assert bar0._total == 10
    assert bar1._total == 10
    assert bar2._total == 10


# -- FilesTab header / elision / columns ----------------------------------


def test_files_header_counts(qtbot):
    tab = FilesTab()
    qtbot.addWidget(tab)
    tab.attach(lambda **kw: [
        {"relative_path": f"f{i}", "size_bytes": 1, "state": "verified"}
        for i in range(3)
    ] if kw.get("offset", 0) == 0 else [])
    tab.set_total(14208)
    assert tab.header_label.text() == "14,208 files · showing 1–3"
    tab.state_combo.setCurrentIndex(1)          # any state filter
    assert tab.header_label.text() == "showing 1–3"
    assert tab.table.horizontalHeader().sectionSize(2) == 204


def test_files_header_total_resets_on_attach(qtbot):
    """attach() (a job switch) must not carry the previous job's total into
    the header before the new job's set_total() round trip arrives — the
    same stale-state failure class ProgressTab.reset() already guards
    against for throughput/events."""
    tab = FilesTab()
    qtbot.addWidget(tab)
    fetcher = lambda **kw: ([{"relative_path": "a", "size_bytes": 1, "state": "verified"}]
                            if kw.get("offset", 0) == 0 else [])
    tab.attach(fetcher)
    tab.set_total(14208)
    assert "14,208" in tab.header_label.text()
    tab.attach(fetcher)          # job switch, new total not yet arrived
    assert "14,208" not in tab.header_label.text()
    assert tab.header_label.text() == "showing 1–1"


def test_files_table_elides_left(qtbot):
    from PySide6.QtCore import Qt
    tab = FilesTab()
    qtbot.addWidget(tab)
    assert tab.table.textElideMode() == Qt.TextElideMode.ElideLeft


def test_inflight_and_events_lists_elide_left(qtbot):
    from PySide6.QtCore import Qt
    tab = ProgressTab()
    qtbot.addWidget(tab)
    assert tab.inflight_list.textElideMode() == Qt.TextElideMode.ElideLeft


# -- Progress headline / route / rate / ETA -------------------------------


def test_progress_headline_and_route(qtbot):
    tab = ProgressTab()
    qtbot.addWidget(tab)
    tab.update_snapshot({
        "name": "IceSeal_Survey_2026_Leg3", "status": "running",
        "direction": "upload", "source_root": r"D:\field\leg3",
        "dest_prefix": "scratch/leg3",
        "progress": {"files_total": 10, "files_done": 5,
                     "bytes_total": 1000, "bytes_done": 620},
    })
    assert tab.headline_name.text() == "IceSeal_Survey_2026_Leg3"
    assert tab.headline_route.text() == r"Upload · D:\field\leg3 → scratch/leg3"
    assert tab.percent_label.text() == "62%"


def test_progress_eta_appears_with_rate(qtbot, monkeypatch):
    import itertools
    times = itertools.count(step=1.0)
    monkeypatch.setattr("mml_cloud_courier.gui.job_tabs.time",
                        type("T", (), {"monotonic": staticmethod(lambda: next(times))}))
    tab = ProgressTab()
    qtbot.addWidget(tab)
    snap = {"status": "running",
            "progress": {"files_total": 1, "files_done": 0,
                         "bytes_total": 1000, "bytes_done": 0}}
    tab.update_snapshot(snap)
    snap2 = {**snap, "progress": {**snap["progress"], "bytes_done": 100}}
    tab.update_snapshot(snap2)           # 100 B/s instant rate
    assert tab.rate_label.text() == "100 B/s"
    assert tab.eta_label.text() != ""    # (1000-100)/100 = 9s remaining


# -- inflight/events role wiring ------------------------------------------


def test_inflight_role_round_trips_through_update_snapshot(qtbot):
    from mml_cloud_courier.gui.progress_widgets import INFLIGHT_ROLE

    tab = ProgressTab()
    qtbot.addWidget(tab)
    entry = {"relative_path": "a/b.tif", "bytes_transferred": 100, "size_bytes": 1000,
             "method": "sliced", "slices_total": 8, "slices_done": 4}
    tab.update_snapshot({
        "progress": {"files_total": 1, "files_done": 0, "bytes_total": 1000, "bytes_done": 100},
        "transferring": [entry],
    })
    assert tab.inflight_list.count() == 1
    item = tab.inflight_list.item(0)
    assert item.data(INFLIGHT_ROLE) == entry
    assert item.text() != ""   # accessible fallback text kept alongside the role data


def test_inflight_title_shows_live_count_and_resets(qtbot):
    tab = ProgressTab()
    qtbot.addWidget(tab)
    assert tab.inflight_title.text() == "IN PROGRESS"

    entries = [{"relative_path": f"f{i}.bin", "bytes_transferred": 1, "size_bytes": 2,
                "method": "single_shot", "slices_total": 0} for i in range(3)]
    tab.update_snapshot({
        "progress": {"files_total": 3, "files_done": 0, "bytes_total": 6, "bytes_done": 3},
        "transferring": entries,
    })
    assert tab.inflight_title.text() == "IN PROGRESS — 3 FILES"   # U+2014 em-dash

    tab.reset()
    assert tab.inflight_title.text() == "IN PROGRESS"


def test_event_role_round_trips_through_append_events(qtbot):
    from mml_cloud_courier.gui.progress_widgets import EVENT_ROLE

    tab = ProgressTab()
    qtbot.addWidget(tab)
    tab._append_events([{"at": "12:00:01", "kind": "verified", "detail": "a/b.tif ok"}])
    assert tab.events_list.count() == 1
    item = tab.events_list.item(0)
    assert item.data(EVENT_ROLE) == ("12:00:01", "verified", "a/b.tif ok")
    assert item.text() != ""   # accessible fallback text kept alongside the role data


def test_events_cap_holds_at_200_for_display_text_and_role_data(qtbot):
    from mml_cloud_courier.gui.progress_widgets import EVENT_ROLE

    tab = ProgressTab()
    qtbot.addWidget(tab)
    events = [{"at": f"t{i}", "kind": "run_started", "detail": ""} for i in range(250)]
    tab._append_events(events)
    assert len(tab._events) == 200
    assert len(tab._event_tuples) == 200
    assert tab.events_list.count() == 200
    # oldest 50 dropped: the surviving window starts at t50, for both the
    # display-text cap and the parallel role-data cap.
    assert tab._event_tuples[0] == ("t50", "run_started", "")
    assert tab.events_list.item(0).data(EVENT_ROLE) == ("t50", "run_started", "")


# -- _verdict_style token usage -----------------------------------------------


def test_verdict_style_cancelled_uses_muted_not_danger(qapp):
    from mml_cloud_courier.gui.job_tabs import _verdict_style
    from mml_cloud_courier.gui import theme

    theme.apply_theme(qapp, theme.LIGHT)
    style = _verdict_style("cancelled")
    assert theme.LIGHT.muted in style
    assert theme.LIGHT.danger_text not in style


def test_verdict_style_incomplete_uses_danger(qapp):
    from mml_cloud_courier.gui.job_tabs import _verdict_style
    from mml_cloud_courier.gui import theme

    theme.apply_theme(qapp, theme.LIGHT)
    style = _verdict_style("incomplete")
    assert theme.LIGHT.danger_text in style


def test_verdict_style_running_uses_muted(qapp):
    from mml_cloud_courier.gui.job_tabs import _verdict_style
    from mml_cloud_courier.gui import theme

    theme.apply_theme(qapp, theme.LIGHT)
    style = _verdict_style("running")
    assert theme.LIGHT.muted in style


def test_summary_tab_refreshes_verdict_on_theme_change(qapp, qtbot):
    from mml_cloud_courier.gui import theme

    theme.apply_theme(qapp, theme.LIGHT)
    tab = _summary_tab(qtbot)
    job = {"status": "complete", "progress": {}, "started_at": None, "finished_at": None}
    tab.update_job(job)

    # Capture initial stylesheet
    initial_style = tab.verdict_label.styleSheet()
    assert theme.LIGHT.accent_soft in initial_style

    # Switch to dark theme
    theme.apply_theme(qapp, theme.DARK)

    # Verdict label should be re-rendered with dark theme tokens
    new_style = tab.verdict_label.styleSheet()
    assert theme.DARK.accent_soft in new_style

    # Clean up: restore LIGHT theme
    theme.apply_theme(qapp, theme.LIGHT)

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.errors_model import ErrorGroup
from mml_cloud_courier.gui.job_tabs import (
    ErrorsTab,
    FilesTab,
    ProgressTab,
    SummaryTab,
    group_fill_rows,
)


def _summary_tab(qtbot) -> SummaryTab:
    tab = SummaryTab(on_open_report=lambda: None, on_resume=lambda: None)
    qtbot.addWidget(tab)
    return tab


# -- group_fill_rows (pure helper, no widget automation needed) --------------


def test_group_fill_rows_appends_more_row_sized_from_group_count():
    """The "...and N more" trailer must come from the group's already-known
    count, not from exhausting every page (that would defeat the point of
    fetching only one page)."""
    page = [f"f{i}.bin" for i in range(500)]
    rows = group_fill_rows(page, group_count=3000)
    assert rows[:500] == page
    assert rows[500] == "…and 2,500 more"
    assert len(rows) == 501


def test_group_fill_rows_no_trailer_when_page_covers_whole_group():
    page = ["a.bin", "b.bin"]
    rows = group_fill_rows(page, group_count=2)
    assert rows == page


def test_group_fill_rows_empty_page():
    assert group_fill_rows([], group_count=0) == []


# -- ErrorsTab lazy expand -----------------------------------------------


def _errors_tab(qtbot, *, on_expand) -> ErrorsTab:
    tab = ErrorsTab(on_retry=lambda c: None, on_exclude=lambda c: None,
                     on_copy=lambda c: None, on_expand=on_expand)
    qtbot.addWidget(tab)
    return tab


def test_errors_tab_expand_fills_one_page_plus_more_row(qtbot):
    group = ErrorGroup(category="network", count=750, quarantined=0,
                        message="Network error.", action="Retry.")
    page = [f"f{i}.bin" for i in range(500)]
    tab = _errors_tab(qtbot, on_expand=lambda category: page)
    tab.load_groups([group])

    item = tab.tree.topLevelItem(0)
    item.setExpanded(True)

    assert item.childCount() == 501
    assert item.child(500).text(0) == "…and 250 more"


def test_errors_tab_expand_failure_clears_filled_role_and_shows_error(qtbot):
    """A mid-fetch exception must not leave the group stuck at "Loading…"
    forever: the fill role is rolled back so a re-expand retries, and the
    user sees a single error row instead of an infinite spinner."""
    group = ErrorGroup(category="network", count=750, quarantined=0,
                        message="Network error.", action="Retry.")

    def failing_expand(category):
        raise RuntimeError("boom")

    tab = _errors_tab(qtbot, on_expand=failing_expand)
    tab.load_groups([group])

    item = tab.tree.topLevelItem(0)
    item.setExpanded(True)

    assert item.childCount() == 1
    assert "Failed to load" in item.child(0).text(0)
    assert "boom" in item.child(0).text(0)

    # Re-expand should retry the fetch (proves _FILLED_ROLE was rolled back;
    # if it hadn't been, _on_item_expanded's early-return guard would skip
    # the fetch and `calls` below would stay empty).
    calls = []

    def now_succeeds(category):
        calls.append(category)
        return ["ok.bin"]

    item.setExpanded(False)
    tab._on_expand = now_succeeds
    item.setExpanded(True)

    assert calls == ["network"]
    # group.count is still 750, so the fill helper adds its "...and N more"
    # trailer for the single-path page too — this only proves the retry ran.
    assert item.child(0).text(0) == "ok.bin"


def test_errors_tab_shows_the_selected_groups_action_text(qtbot):
    """The taxonomy's suggested action ("what to do about it") must be
    visible in the tab, not just delivered by the API — with the first
    group auto-selected so a single-cause job needs no click."""
    locked = ErrorGroup(category="file_locked", count=1, quarantined=0,
                        message="The file is open in another program.",
                        action="Close the program holding the file, then resume the job.")
    denied = ErrorGroup(category="permission_denied", count=3, quarantined=0,
                        message="Access to this file was denied.",
                        action="Grant the transfer service account read access to this path.")
    tab = _errors_tab(qtbot, on_expand=lambda category: [])

    tab.load_groups([locked, denied])
    assert tab.action_label.text() == locked.action   # auto-selected first group

    tab.tree.setCurrentItem(tab.tree.topLevelItem(1))
    assert tab.action_label.text() == denied.action

    tab.load_groups([])
    assert tab.action_label.text() == ""


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

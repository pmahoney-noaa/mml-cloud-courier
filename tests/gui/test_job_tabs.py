import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_transfer.gui.job_tabs import SummaryTab


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

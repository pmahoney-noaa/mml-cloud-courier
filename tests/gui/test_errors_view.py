import pytest

from mml_cloud_courier.gui.errors_model import ErrorGroup
from mml_cloud_courier.gui.errors_view import (
    ErrorsTab, group_fill_rows, group_tone, header_sentence, order_groups,
)


def _group(category, count, message="msg", action="act", quarantined=0):
    return ErrorGroup(category=category, count=count, quarantined=quarantined,
                      message=message, action=action)


def test_group_tone():
    assert group_tone("permission_denied") == "danger"
    assert group_tone("credential") == "danger"
    assert group_tone("file_locked") == "warn"
    assert group_tone("source_changed") == "warn"
    assert group_tone("network") == "accent"
    assert group_tone("never_heard_of_it") == "danger"   # unknown = needs you


def test_order_groups_needs_you_first_then_count():
    groups = [_group("network", 400), _group("credential", 2),
              _group("file_locked", 6), _group("permission_denied", 15)]
    ordered = order_groups(groups)
    assert [g.category for g in ordered] == [
        "permission_denied", "credential", "network", "file_locked"]


def test_header_sentence_counts():
    groups = [_group("credential", 2), _group("permission_denied", 15),
              _group("network", 400), _group("file_locked", 6)]
    text = header_sentence(groups, files_total=61022)
    assert text == ("423 of 61,022 files did not transfer, from 4 causes."
                    " 2 clear themselves; 2 need something from you.")
    assert header_sentence([], files_total=10) == ""
    one = header_sentence([_group("network", 3)], files_total=10)
    assert one == ("3 of 10 files did not transfer, from 1 cause."
                   " 1 clears itself; 0 need something from you.")


def test_cards_built_per_group_with_own_buttons(qtbot):
    calls = []
    tab = ErrorsTab(on_retry=lambda c: calls.append(("retry", c)),
                    on_exclude=lambda c: calls.append(("exclude", c)),
                    on_copy=lambda c: calls.append(("copy", c)),
                    on_expand=lambda c: [f"{c}/a.bin"])
    qtbot.addWidget(tab)
    tab.load_groups([_group("network", 400), _group("credential", 2)])
    assert tab.group_count() == 2
    first = tab.card(0)                      # ordered: credential (needs-you) first
    assert first.group.category == "credential"
    assert first.tag.text() == "Needs you"
    assert tab.card(1).tag.text() == "Retries on its own"
    first.retry_button.click()
    tab.card(1).copy_button.click()
    assert calls == [("retry", "credential"), ("copy", "network")]
    # sample rows fetched via on_expand, with the "…and N more" trailer
    assert "credential/a.bin" in first.samples_label.text()
    assert "…and 1 more" in first.samples_label.text()


# -- group_fill_rows (pure helper, moved verbatim from job_tabs) -------------


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


# -- per-card reworks of the old tree/selection-based ErrorsTab tests --------
#
# The old ErrorsTab was a QTreeWidget: groups were top-level items, lazily
# expanded on click, with one selection driving a single shared action_label
# and a single set of buttons. The new ErrorsTab has no selection model —
# every group renders as its own always-expanded ErrorCard with its own
# action_label, samples_label and buttons, and all groups' sample rows are
# fetched up front (bounded to one page each) during load_groups. These three
# tests carry forward the intent of the three tree-based tests they replace.


def _errors_tab(qtbot, *, on_expand) -> ErrorsTab:
    tab = ErrorsTab(on_retry=lambda c: None, on_exclude=lambda c: None,
                     on_copy=lambda c: None, on_expand=on_expand)
    qtbot.addWidget(tab)
    return tab


def test_error_card_samples_show_first_three_plus_trailer_sized_from_count(qtbot):
    """Replaces test_errors_tab_expand_fills_one_page_plus_more_row: the old
    tree filled all 500 fetched rows into children plus a trailer; the new
    card only ever shows 3 sample rows (a fixed-height header line, not a
    scrolling list) but the trailer is still sized from the group's true
    count, not from the fetched page length."""
    group = ErrorGroup(category="network", count=750, quarantined=0,
                        message="Network error.", action="Retry.")
    page = [f"f{i}.bin" for i in range(500)]
    tab = _errors_tab(qtbot, on_expand=lambda category: page)
    tab.load_groups([group])

    card = tab.card(0)
    assert card.samples_label.text() == "f0.bin · f1.bin · f2.bin · …and 747 more"


def test_error_card_samples_degrade_gracefully_when_expand_fails(qtbot):
    """Replaces test_errors_tab_expand_failure_clears_filled_role_and_shows_error:
    the old tree left a single "Failed to load: <exc>" row and rolled back a
    _FILLED_ROLE flag so a later re-expand would retry. There is no per-item
    expand state anymore — on_expand runs once per group inside load_groups,
    so a raised exception must not crash card construction (samples just fall
    back to the "…and N more" trailer for the whole group), and calling
    load_groups again (the natural retry path — e.g. the next refresh) with a
    working on_expand recovers cleanly."""
    group = ErrorGroup(category="network", count=750, quarantined=0,
                        message="Network error.", action="Retry.")

    def failing_expand(category):
        raise RuntimeError("boom")

    tab = _errors_tab(qtbot, on_expand=failing_expand)
    tab.load_groups([group])

    card = tab.card(0)
    assert card.samples_label.text() == "…and 750 more"

    calls = []

    def now_succeeds(category):
        calls.append(category)
        return ["ok.bin"]

    tab._on_expand = now_succeeds
    tab.load_groups([group])

    assert calls == ["network"]
    assert tab.card(0).samples_label.text() == "ok.bin · …and 749 more"


def test_error_card_action_text_shown_per_card_not_via_selection(qtbot):
    """Replaces test_errors_tab_shows_the_selected_groups_action_text: the
    old tab auto-selected the first group and showed one shared action_label
    that changed on selection, clearing to "" with no groups. Now every card
    is always-expanded and carries its own action_label — nothing needs to be
    selected to see it, and an empty group list simply produces zero cards."""
    locked = ErrorGroup(category="file_locked", count=1, quarantined=0,
                        message="The file is open in another program.",
                        action="Close the program holding the file, then resume the job.")
    denied = ErrorGroup(category="permission_denied", count=3, quarantined=0,
                        message="Access to this file was denied.",
                        action="Grant the transfer service account read access to this path.")
    tab = _errors_tab(qtbot, on_expand=lambda category: [])

    tab.load_groups([locked, denied])
    assert tab.group_count() == 2
    # ordered: permission_denied (needs-you) first, file_locked (self-clearing) second
    assert tab.card(0).group.category == "permission_denied"
    assert tab.card(0).action_label.text() == denied.action
    assert tab.card(1).group.category == "file_locked"
    assert tab.card(1).action_label.text() == locked.action

    tab.load_groups([])
    assert tab.group_count() == 0

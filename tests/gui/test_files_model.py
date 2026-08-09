import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QModelIndex, Qt

from mml_cloud_courier.gui.files_model import PAGE, FileTableModel


def _rows(n, state="verified"):
    return [{"relative_path": f"f{i}.bin", "size_bytes": 1000 + i,
             "state": state, "error_message": None} for i in range(n)]


class RecordingFetcher:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, *, state=None, category=None, limit=PAGE, offset=0):
        self.calls.append({"state": state, "category": category,
                           "limit": limit, "offset": offset})
        return self.rows[offset:offset + limit]


def test_pages_are_fetched_on_demand(qapp):
    fetcher = RecordingFetcher(_rows(1200))
    model = FileTableModel(fetcher)
    model.set_filter()

    assert model.rowCount() == PAGE
    assert model.canFetchMore(QModelIndex())
    model.fetchMore(QModelIndex())
    assert model.rowCount() == 1000
    model.fetchMore(QModelIndex())
    assert model.rowCount() == 1200
    assert not model.canFetchMore(QModelIndex())   # short page = exhausted
    assert [c["offset"] for c in fetcher.calls] == [0, 500, 1000]


def test_data_renders_human_values(qapp):
    model = FileTableModel(RecordingFetcher(_rows(1)))
    model.set_filter()
    index = model.index(0, 1)
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "1.0 KB"
    assert model.data(model.index(0, 2), Qt.ItemDataRole.DisplayRole) == "Verified"


def test_set_filter_passes_through_and_resets(qapp):
    fetcher = RecordingFetcher(_rows(3, state="failed"))
    model = FileTableModel(fetcher)
    model.set_filter(state="failed", category="file_locked")
    assert fetcher.calls[-1]["state"] == "failed"
    assert fetcher.calls[-1]["category"] == "file_locked"
    assert model.rowCount() == 3


def test_fetcher_exception_does_not_escape(qapp):
    class FailingFetcher:
        def __call__(self, **kw):
            raise RuntimeError("boom")

    model = FileTableModel(FailingFetcher())
    model.set_filter()  # should not raise
    assert model.rowCount() == 0
    assert not model.canFetchMore(QModelIndex())
    assert model.last_error == "boom"


def test_error_recovery_on_refresh(qapp):
    class StatefulFetcher:
        def __init__(self):
            self.call_count = 0

        def __call__(self, **kw):
            self.call_count += 1
            if self.call_count == 1:
                raise RuntimeError("transient")
            return _rows(2)

    fetcher = StatefulFetcher()
    model = FileTableModel(fetcher)
    model.set_filter()  # first call fails
    assert model.rowCount() == 0
    assert model.last_error == "transient"

    model.refresh()  # second call succeeds
    assert model.rowCount() == 2
    assert model.last_error is None


def test_state_column_has_no_decoration_swatch(qapp):
    # Wave 2, item 2: the swatch is gone -- state is color-coded via text
    # only now.
    model = FileTableModel(RecordingFetcher(_rows(1, state="verified")))
    model.set_filter()
    assert model.data(model.index(0, 2), Qt.ItemDataRole.DecorationRole) is None


def test_state_column_foreground_is_accent_text_for_verified(qapp):
    from mml_cloud_courier.gui import theme

    theme.apply_theme(qapp, theme.LIGHT)
    model = FileTableModel(RecordingFetcher(_rows(1, state="verified")))
    model.set_filter()
    color = model.data(model.index(0, 2), Qt.ItemDataRole.ForegroundRole)
    assert color.name() == theme._qcolor(theme.LIGHT.accent_text).name()
    theme.apply_theme(qapp, theme.LIGHT)   # leave global state clean


def test_state_column_foreground_is_danger_text_for_failure_states(qapp):
    from mml_cloud_courier.gui import theme

    theme.apply_theme(qapp, theme.LIGHT)
    model = FileTableModel(RecordingFetcher(_rows(1, state="quarantined")))
    model.set_filter()
    color = model.data(model.index(0, 2), Qt.ItemDataRole.ForegroundRole)
    assert color.name() == theme._qcolor(theme.LIGHT.danger_text).name()

    model = FileTableModel(RecordingFetcher(_rows(1, state="failed")))
    model.set_filter()
    color = model.data(model.index(0, 2), Qt.ItemDataRole.ForegroundRole)
    assert color.name() == theme._qcolor(theme.LIGHT.danger_text).name()


def test_state_column_foreground_falls_back_to_muted_for_unknown_state(qapp):
    from mml_cloud_courier.gui import theme

    theme.apply_theme(qapp, theme.LIGHT)
    model = FileTableModel(RecordingFetcher(_rows(1, state="bogus_state")))
    model.set_filter()
    color = model.data(model.index(0, 2), Qt.ItemDataRole.ForegroundRole)
    assert color.name() == theme._qcolor(theme.LIGHT.muted).name()


def test_files_tab_hides_row_number_header(qtbot):
    from mml_cloud_courier.gui.job_tabs import FilesTab

    tab = FilesTab()
    qtbot.addWidget(tab)
    assert not tab.table.verticalHeader().isVisible()


def test_path_column_tooltip_is_full_path():
    from PySide6.QtCore import Qt
    model = FileTableModel(lambda **kw: [])
    model._rows = [{"relative_path": "leg3/imagery/IMG_1147.tif",
                    "size_bytes": 5, "state": "verified"}]
    index = model.index(0, 0)
    assert model.data(index, Qt.ItemDataRole.ToolTipRole) == "leg3/imagery/IMG_1147.tif"
    assert model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole) is None

import pytest

pytest.importorskip("PySide6")

from mml_cloud_courier.gui.errors_model import (
    ErrorGroup, build_error_groups, fetch_group_page, fetch_group_paths,
)


def test_build_error_groups_formats_labels():
    groups = build_error_groups([
        {"category": "permission_denied", "count": 3000, "quarantined": 0,
         "message": "Access to this file was denied.", "action": "Grant access."},
        {"category": "file_locked", "count": 1, "quarantined": 1,
         "message": "The file is open in another program.", "action": "Close it."},
    ])
    assert groups[0].label == "Access to this file was denied. — 3,000 files"
    assert groups[1].label == "The file is open in another program. — 1 file"


class PagingFakeClient:
    def __init__(self, total):
        self._paths = [f"f{i}.bin" for i in range(total)]
        self.calls = 0

    def files(self, job_id, *, state=None, category=None, limit=500, offset=0):
        self.calls += 1
        return [{"relative_path": p} for p in self._paths[offset:offset + limit]]


def test_fetch_group_paths_pages_until_short_page():
    client = PagingFakeClient(750)
    paths = fetch_group_paths(client, 1, "network")
    assert len(paths) == 750
    assert client.calls == 2


def test_fetch_group_paths_respects_the_cap():
    client = PagingFakeClient(5000)
    assert len(fetch_group_paths(client, 1, "network", cap=1000)) == 1000


def test_fetch_group_page_fetches_exactly_one_page():
    """The errors-tab tree-expand path must never walk multiple pages
    (that's fetch_group_paths' job, run async for copy-to-clipboard) —
    a bounded single GET keeps the synchronous Qt-thread expand fast."""
    client = PagingFakeClient(5000)
    page = fetch_group_page(client, 1, "network")
    assert len(page) == 500
    assert client.calls == 1
    assert page == [f"f{i}.bin" for i in range(500)]


def test_fetch_group_page_returns_short_page_without_extra_call():
    client = PagingFakeClient(200)
    page = fetch_group_page(client, 1, "network")
    assert len(page) == 200
    assert client.calls == 1

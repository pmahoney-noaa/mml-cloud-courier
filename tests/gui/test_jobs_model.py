import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.jobs_model import (
    GROUP_LABELS, JOB_ID_ROLE, RAIL_GROUPS, STATUS_ROLE,
    build_rail_model, group_for_status, rail_job_ids, sync_rail,
)


def _job(job_id, status, name="j", scheduled=None):
    return {"id": job_id, "name": name, "status": status,
            "direction": "upload", "source_root": "C:\\d", "dest_prefix": "",
            "scheduled_start_at": scheduled, "created_at": "2026-08-06T00:00:00+00:00"}


def test_every_job_status_lands_in_a_group():
    assert group_for_status("incomplete") == "needs_attention"
    assert group_for_status("stalled") == "needs_attention"
    assert group_for_status("paused") == "needs_attention"
    assert group_for_status("running") == "running"
    assert group_for_status("scanning") == "running"
    assert group_for_status("pending") == "queued"
    assert group_for_status("complete") == "completed"
    assert group_for_status("cancelled") == "completed"
    assert group_for_status("who-knows") == "needs_attention"


def test_needs_attention_is_pinned_first():
    assert RAIL_GROUPS[0] == "needs_attention"


def test_sync_rail_places_jobs_and_roles(qapp):
    model = build_rail_model()
    sync_rail(model, [_job(1, "complete"), _job(2, "incomplete"), _job(3, "running")])

    assert model.rowCount() == 4
    attention = model.item(0)
    assert attention.text() == GROUP_LABELS["needs_attention"]
    child = attention.child(0)
    assert child.data(JOB_ID_ROLE) == 2
    assert child.data(STATUS_ROLE) == "incomplete"
    assert "needs attention" in child.text().lower()
    assert rail_job_ids(model) == [2, 3, 1]


def test_sync_rail_annotates_scheduled_jobs(qapp):
    model = build_rail_model()
    sync_rail(model, [_job(5, "pending", scheduled="2026-08-07T02:30:00+00:00")])
    queued = model.item(RAIL_GROUPS.index("queued"))
    assert "starts" in queued.child(0).text()


def test_sync_rail_is_idempotent_and_moves_jobs(qapp):
    model = build_rail_model()
    sync_rail(model, [_job(1, "running")])
    sync_rail(model, [_job(1, "complete")])
    assert rail_job_ids(model) == [1]
    assert model.item(RAIL_GROUPS.index("completed")).rowCount() == 1
    assert model.item(RAIL_GROUPS.index("running")).rowCount() == 0

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from mml_cloud_courier.gui.jobs_model import (
    GROUP_LABELS, JOB_ID_ROLE, RAIL_GROUPS, SECOND_LINE_ROLE, STATUS_ROLE,
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
    assert "needs attention" in child.data(SECOND_LINE_ROLE).lower()
    assert rail_job_ids(model) == [2, 3, 1]


def test_sync_rail_annotates_scheduled_jobs(qapp):
    model = build_rail_model()
    sync_rail(model, [_job(5, "pending", scheduled="2026-08-07T02:30:00+00:00")])
    queued = model.item(RAIL_GROUPS.index("queued"))
    assert "starts" in queued.child(0).data(SECOND_LINE_ROLE)


def test_sync_rail_is_idempotent_and_moves_jobs(qapp):
    model = build_rail_model()
    sync_rail(model, [_job(1, "running")])
    sync_rail(model, [_job(1, "complete")])
    assert rail_job_ids(model) == [1]
    assert model.item(RAIL_GROUPS.index("completed")).rowCount() == 1
    assert model.item(RAIL_GROUPS.index("running")).rowCount() == 0


def test_sync_rail_returns_true_on_first_and_changed_calls(qapp):
    model = build_rail_model()
    assert sync_rail(model, [_job(1, "running")]) is True
    # a status change (different group) is a change
    assert sync_rail(model, [_job(1, "complete")]) is True


def test_sync_rail_returns_false_and_leaves_items_untouched_on_identical_input(qapp):
    model = build_rail_model()
    jobs = [_job(1, "running"), _job(2, "incomplete")]
    assert sync_rail(model, jobs) is True
    running_child = model.item(RAIL_GROUPS.index("running")).child(0)

    assert sync_rail(model, jobs) is False
    # not just equal-looking data -- the identical item object, proving no
    # removeRows/appendRow churn happened on the no-op path.
    assert model.item(RAIL_GROUPS.index("running")).child(0) is running_child
    assert rail_job_ids(model) == [2, 1]


def test_sync_rail_returns_true_when_only_service_up_flips(qapp):
    # A stall/recovery changes every running/scanning row's second line
    # (STALLED_OVERRIDE) without touching any job field -- service_up must
    # be part of the signature or this would wrongly look unchanged.
    model = build_rail_model()
    jobs = [_job(1, "running")]
    sync_rail(model, jobs, service_up=True)
    assert sync_rail(model, jobs, service_up=False) is True
    assert sync_rail(model, jobs, service_up=False) is False   # now stable


def test_rail_row_lines_split_name_and_status():
    from mml_cloud_courier.gui.jobs_model import rail_row_lines
    job = {"id": 121, "name": "IceSeal_Survey_2026_Leg3", "status": "running"}
    line1, line2 = rail_row_lines(job)
    assert line1 == "#121 IceSeal_Survey_2026_Leg3"
    assert line2 == "Running"
    _, down = rail_row_lines(job, service_up=False)
    assert down == "Stalled — service stopped"


def test_rail_row_lines_keeps_schedule_suffix():
    from mml_cloud_courier.gui.jobs_model import rail_row_lines
    job = {"id": 5, "name": "n", "status": "pending",
           "scheduled_start_at": "2026-08-09T02:00:00+00:00"}
    _, line2 = rail_row_lines(job)
    assert line2.startswith("Queued — starts ")


def test_job_item_roles_carry_second_line():
    from mml_cloud_courier.gui.jobs_model import SECOND_LINE_ROLE, _job_item
    item = _job_item({"id": 7, "name": "leg3", "status": "running"})
    assert item.text() == "#7 leg3"
    assert item.data(SECOND_LINE_ROLE) == "Running"


def test_rail_row_lines_stalled_override_matches_constant():
    from mml_cloud_courier.gui.jobs_model import STALLED_OVERRIDE, rail_row_lines
    line2 = rail_row_lines({"id": 1, "name": "n", "status": "running"}, service_up=False)[1]
    assert line2 == STALLED_OVERRIDE


def test_delegate_dot_token_warns_on_stalled_override():
    # The override row (real status "running", but the service is down and
    # the second line reads "Stalled - service stopped") must show a warn
    # dot rather than the accent_2 the raw "running" status would map to.
    from mml_cloud_courier.gui.jobs_model import STALLED_OVERRIDE
    from mml_cloud_courier.gui.rail_delegate import _dot_token
    assert _dot_token("running", STALLED_OVERRIDE) == "warn"
    assert _dot_token("running", "Running") == "accent_2"
    assert _dot_token("incomplete", "Needs attention") == "danger"

"""The left rail: jobs grouped by status, Needs attention pinned on top.

A QStandardItemModel with four permanent group rows; sync_rail replaces
each group's children when the incoming jobs actually differ from the
last sync (see the signature check below) -- otherwise it's a no-op.
Cheap and correct for the tens of jobs a workstation accumulates;
selection is preserved by the view re-selecting the remembered job id
after a sync that actually rebuilt rows.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from mml_cloud_courier.gui.format import STATUS_LABELS, human_schedule

RAIL_GROUPS = ("needs_attention", "running", "queued", "completed")
GROUP_LABELS = {
    "needs_attention": "Needs attention",
    "running": "Running",
    "queued": "Queued",
    "completed": "Completed",
}
_GROUP_FOR_STATUS = {
    "incomplete": "needs_attention",
    "stalled": "needs_attention",
    "paused": "needs_attention",
    "running": "running",
    "scanning": "running",
    "pending": "queued",
    "complete": "completed",
    "cancelled": "completed",
}
JOB_ID_ROLE = Qt.ItemDataRole.UserRole + 1
STATUS_ROLE = Qt.ItemDataRole.UserRole + 2
SECOND_LINE_ROLE = Qt.ItemDataRole.UserRole + 3

# The second-line text substituted when a job looks active but the service
# is down -- lives here (not in the delegate) so the literal exists once.
STALLED_OVERRIDE = "Stalled — service stopped"


def group_for_status(status: str) -> str:
    # Unknown statuses surface at the top rather than vanishing.
    return _GROUP_FOR_STATUS.get(status, "needs_attention")


def build_rail_model() -> QStandardItemModel:
    model = QStandardItemModel()
    for group in RAIL_GROUPS:
        item = QStandardItem(GROUP_LABELS[group])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # a header, not a choice
        model.appendRow(item)
    return model


def rail_row_lines(job: dict, service_up: bool = True) -> tuple[str, str]:
    if not service_up and job["status"] in ("running", "scanning"):
        status = STALLED_OVERRIDE
    else:
        status = STATUS_LABELS.get(job["status"], job["status"])
    if job["status"] == "pending" and job.get("scheduled_start_at"):
        status += f" — starts {human_schedule(job['scheduled_start_at'])}"
    return f"#{job['id']} {job['name']}", status


def _job_item(job: dict, service_up: bool = True) -> QStandardItem:
    line1, line2 = rail_row_lines(job, service_up)
    item = QStandardItem(line1)
    item.setData(job["id"], JOB_ID_ROLE)
    item.setData(job["status"], STATUS_ROLE)
    item.setData(line2, SECOND_LINE_ROLE)
    item.setEditable(False)
    return item


def _grouped_and_sorted(jobs: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {group: [] for group in RAIL_GROUPS}
    for job in jobs:
        buckets[group_for_status(job["status"])].append(job)
    for group in RAIL_GROUPS:
        buckets[group].sort(key=lambda j: j["id"], reverse=True)
    return buckets


def _rail_signature(buckets: dict[str, list[dict]], service_up: bool) -> tuple:
    # Ordered per group, same sort sync_rail itself applies -- a reorder
    # (e.g. a new job landing above an old one) must count as a change.
    # service_up is included: a stall/recovery flips every "running"/
    # "scanning" row's second line (STALLED_OVERRIDE) without touching any
    # job's own fields, so it must force a rebuild on its own.
    return (
        service_up,
        tuple(
            tuple((job["id"], job["status"], job["name"], job.get("scheduled_start_at"))
                 for job in buckets[group])
            for group in RAIL_GROUPS
        ),
    )


def sync_rail(model: QStandardItemModel, jobs: list[dict], service_up: bool = True) -> bool:
    """Rebuild the rail's job rows from `jobs`, returning True iff rows
    were actually rebuilt. When nothing has changed since the last sync
    (same signature) and the groups already have children, this is a
    deliberate no-op -- MainWindow._on_jobs only re-selects/re-shows the
    remembered job when this returns True, because QTreeView.setCurrentIndex
    auto-expands the selection's ancestors, which would silently reopen a
    group the user just collapsed on every poll tick otherwise."""
    buckets = _grouped_and_sorted(jobs)
    signature = _rail_signature(buckets, service_up)
    already_populated = any(model.item(i).rowCount() for i in range(len(RAIL_GROUPS)))
    if signature == getattr(model, "_rail_signature", None) and already_populated:
        return False
    model._rail_signature = signature

    for index, group in enumerate(RAIL_GROUPS):
        parent = model.item(index)
        parent.removeRows(0, parent.rowCount())
        for job in buckets[group]:
            parent.appendRow(_job_item(job, service_up))
    return True


def rail_job_ids(model: QStandardItemModel) -> list[int]:
    ids: list[int] = []
    for index in range(model.rowCount()):
        parent = model.item(index)
        ids += [parent.child(r).data(JOB_ID_ROLE) for r in range(parent.rowCount())]
    return ids

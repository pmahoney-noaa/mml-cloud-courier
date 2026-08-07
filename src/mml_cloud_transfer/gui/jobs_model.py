"""The left rail: jobs grouped by status, Needs attention pinned on top.

A QStandardItemModel with four permanent group rows; sync_rail replaces
each group's children on every poll. Cheap and correct for the tens of
jobs a workstation accumulates; selection is preserved by the view
re-selecting the remembered job id after sync.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from mml_cloud_transfer.gui.format import STATUS_LABELS, human_schedule
from mml_cloud_transfer.gui.icons import group_icon

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


def group_for_status(status: str) -> str:
    # Unknown statuses surface at the top rather than vanishing.
    return _GROUP_FOR_STATUS.get(status, "needs_attention")


def build_rail_model() -> QStandardItemModel:
    model = QStandardItemModel()
    for group in RAIL_GROUPS:
        item = QStandardItem(GROUP_LABELS[group])
        item.setIcon(group_icon(group))
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)   # a header, not a choice
        model.appendRow(item)
    return model


def _job_item(job: dict) -> QStandardItem:
    label = STATUS_LABELS.get(job["status"], job["status"])
    text = f"#{job['id']} {job['name']} — {label}"
    if job["status"] == "pending" and job.get("scheduled_start_at"):
        text += f" — starts {human_schedule(job['scheduled_start_at'])}"
    item = QStandardItem(text)
    item.setData(job["id"], JOB_ID_ROLE)
    item.setData(job["status"], STATUS_ROLE)
    item.setEditable(False)
    return item


def sync_rail(model: QStandardItemModel, jobs: list[dict]) -> None:
    buckets: dict[str, list[dict]] = {group: [] for group in RAIL_GROUPS}
    for job in jobs:
        buckets[group_for_status(job["status"])].append(job)
    for index, group in enumerate(RAIL_GROUPS):
        parent = model.item(index)
        parent.removeRows(0, parent.rowCount())
        for job in sorted(buckets[group], key=lambda j: j["id"], reverse=True):
            parent.appendRow(_job_item(job))


def rail_job_ids(model: QStandardItemModel) -> list[int]:
    ids: list[int] = []
    for index in range(model.rowCount()):
        parent = model.item(index)
        ids += [parent.child(r).data(JOB_ID_ROLE) for r in range(parent.rowCount())]
    return ids

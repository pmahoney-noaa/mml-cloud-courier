"""Advanced settings dialog over the settings API.

Exposes workers count, auto-resume flag, and size-policy override via spinboxes
and radio buttons. Loads/saves via call_async to keep the Qt loop responsive.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QCheckBox,
    QVBoxLayout,
    QHBoxLayout,
)

from mml_cloud_transfer.gui.workers import call_async

MIB = 1024 * 1024


def policy_text(single_mib: int, resumable_mib: int, slice_mib: int) -> str:
    """Convert MiB spinbox values to the CSV byte-string format."""
    return f"{single_mib * MIB},{resumable_mib * MIB},{slice_mib * MIB}"


def policy_fields(text: str | None) -> tuple[int, int, int] | None:
    """Convert CSV byte-string format to MiB spinbox values, or None if text is None."""
    if text is None:
        return None
    single, resumable, min_slice = (int(part) for part in text.split(","))
    return (single // MIB, resumable // MIB, min_slice // MIB)


class SettingsDialog(QDialog):
    """Settings dialog with workers count, auto-resume, and size-policy override."""

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self._had_stored_policy = False
        self._policy_dirty = False
        self._loading = False
        self.setWindowTitle("Advanced Settings")

        # Workers spinbox
        self.workers_spin = QSpinBox()
        self.workers_spin.setMinimum(1)
        self.workers_spin.setMaximum(16)
        self.workers_spin.setValue(1)

        # Auto-resume checkbox
        self.auto_resume_check = QCheckBox()

        # Size policy group
        policy_group = QGroupBox("Transfer size policy")
        policy_layout = QVBoxLayout()

        self.default_radio = QRadioButton("Use service defaults")
        self.default_radio.setChecked(True)
        policy_layout.addWidget(self.default_radio)

        self.custom_policy_radio = QRadioButton("Custom policy")
        policy_layout.addWidget(self.custom_policy_radio)

        # Custom policy spinboxes
        custom_form = QFormLayout()
        self.single_spin = QSpinBox()
        self.single_spin.setMinimum(1)
        self.single_spin.setMaximum(65536)
        self.single_spin.setValue(1)
        custom_form.addRow("Single-shot max (MiB):", self.single_spin)

        self.resumable_spin = QSpinBox()
        self.resumable_spin.setMinimum(1)
        self.resumable_spin.setMaximum(65536)
        self.resumable_spin.setValue(1)
        custom_form.addRow("Resumable max (MiB):", self.resumable_spin)

        self.slice_spin = QSpinBox()
        self.slice_spin.setMinimum(1)
        self.slice_spin.setMaximum(65536)
        self.slice_spin.setValue(1)
        custom_form.addRow("Minimum slice (MiB):", self.slice_spin)

        policy_layout.addLayout(custom_form)
        policy_group.setLayout(policy_layout)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        # Save and Close buttons
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.save_button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        # Main layout
        form = QFormLayout()
        form.addRow("Concurrent file transfers:", self.workers_spin)
        form.addRow("Resume interrupted jobs on startup:", self.auto_resume_check)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(policy_group)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

        # Connect signals to track user edits (dirty flag)
        self.single_spin.valueChanged.connect(self._mark_policy_dirty)
        self.resumable_spin.valueChanged.connect(self._mark_policy_dirty)
        self.slice_spin.valueChanged.connect(self._mark_policy_dirty)
        self.custom_policy_radio.toggled.connect(self._mark_policy_dirty)
        self.default_radio.toggled.connect(self._mark_policy_dirty)

        # Load settings on construction
        self.status_label.setText("Loading settings…")
        call_async(self.client.get_settings, parent=self,
                   on_done=self._settings_loaded, on_failed=self._load_failed)

    def _settings_loaded(self, result):
        """Update UI with loaded settings.

        Values in ``stored`` are staged overrides awaiting a service restart;
        they take priority over the top-level running values so a reopen-then-Save
        never silently reverts a staged change back to what's currently running.
        """
        self._loading = True
        try:
            stored = result.get("stored", {})
            self.workers_spin.setValue(
                stored.get("file_workers", result.get("file_workers", 1))
            )
            self.auto_resume_check.setChecked(
                stored.get("auto_resume_on_startup", result.get("auto_resume_on_startup", False))
            )

            # Check if there's a stored policy override
            self._had_stored_policy = "size_policy" in result.get("stored", {})
            if self._had_stored_policy:
                stored_policy = result["stored"].get("size_policy")
                if stored_policy:
                    fields = policy_fields(stored_policy)
                    if fields:
                        single, resumable, min_slice = fields
                        self.single_spin.setValue(single)
                        self.resumable_spin.setValue(resumable)
                        self.slice_spin.setValue(min_slice)
                        self.custom_policy_radio.setChecked(True)
                        self.default_radio.setChecked(False)

            self.status_label.setText("")
        finally:
            self._loading = False
            self._policy_dirty = False

    def _load_failed(self, message):
        """Show load error."""
        self.status_label.setText(f"Failed to load settings: {message}")

    def _mark_policy_dirty(self):
        """Mark size policy as dirty (user has edited it) unless loading."""
        if not self._loading:
            self._policy_dirty = True

    def build_payload(self) -> dict:
        """Build the PUT payload for put_settings.

        Dirty-tracking ensures non-aligned stored policies are never silently
        rewritten on untouched Save. The logic:
        - custom checked AND dirty → include new size_policy
        - custom checked AND NOT dirty → omit (leave server's value untouched)
        - default checked AND _had_stored_policy → include size_policy: "" (clear)
        - otherwise omit
        """
        payload = {
            "file_workers": self.workers_spin.value(),
            "auto_resume_on_startup": self.auto_resume_check.isChecked(),
        }
        if self.custom_policy_radio.isChecked():
            if self._policy_dirty:
                payload["size_policy"] = policy_text(
                    self.single_spin.value(), self.resumable_spin.value(),
                    self.slice_spin.value(),
                )
            # If custom is checked but NOT dirty, omit size_policy (leave server untouched)
        elif self._had_stored_policy:
            payload["size_policy"] = ""   # explicit clear back to defaults
        return payload

    def _save(self):
        """Save settings via put_settings."""
        self.status_label.setText("Saving…")
        self.save_button.setEnabled(False)
        call_async(lambda: self.client.put_settings(self.build_payload()),
                   parent=self, on_done=self._save_done, on_failed=self._save_failed)

    def _save_done(self, result):
        """Show success message after save and refresh flags from response."""
        # Refresh _had_stored_policy from the PUT response (it has the same shape as GET)
        self._had_stored_policy = "size_policy" in result.get("stored", {})
        # Reset dirty flag: user's edits are now persisted
        self._policy_dirty = False

        self.status_label.setText(
            "Saved. Changes take effect the next time the transfer service starts."
        )
        self.save_button.setEnabled(True)

    def _save_failed(self, message):
        """Show error message after failed save."""
        self.status_label.setText(message)
        self.save_button.setEnabled(True)

"""Client-agnostic visual primitives for the connections manager and the
New-connection stepper. QSS-styled parts ride the app-level stylesheet
(theme.qss) and restyle automatically; custom-painted parts read
theme.current() in paintEvent and repaint on theme.notifier.changed via a
bound-method connection, which Qt drops automatically when the widget dies."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mml_cloud_courier.gui import theme
from mml_cloud_courier.gui.format import human_ago, iso_age_days

AUTH_PRESENTATION: dict[str, tuple[str, str]] = {
    "service_account_key": ("SERVICE ACCOUNT KEY", "accent"),
    "oauth_user": ("GOOGLE SIGN-IN", "warn"),
    "adc": ("COMMAND-LINE CREDENTIALS", "track"),
}
ADC_NOTE = ("Created outside this app. It works, but only this machine's"
            " signed-in account can use it.")
STALE_OAUTH_DAYS = 7


def last_check_line(profile: dict) -> tuple[str, str]:
    """The card's third row: (text, tone). Tone is 'muted' or 'warn'.
    No capability claims from list data — list_profiles has none (spec §9.1)."""
    auth = profile.get("auth_type")
    if auth == "adc":
        return ADC_NOTE, "muted"
    at = profile.get("validated_at")
    if not at:
        return "Never checked.", "muted"
    when = human_ago(at)
    age = iso_age_days(at)
    if auth == "oauth_user" and age is not None and age > STALE_OAUTH_DAYS:
        return (f"Checked {when} — this sign-in may have expired."
                " Check it before the next transfer.", "warn")
    return f"Checked {when}.", "muted"


def repolish(widget: QWidget) -> None:
    """Re-evaluate QSS after a dynamic property change, descendants included."""
    widgets = [widget, *widget.findChildren(QWidget)]
    for w in widgets:
        w.style().unpolish(w)
        w.style().polish(w)


def pill_font() -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
    font.setPixelSize(10)
    font.setWeight(QFont.Weight.Medium)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
    return font


def section_font() -> QFont:
    font = pill_font()
    font.setWeight(QFont.Weight.Normal)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    return font


class Pill(QLabel):
    def __init__(self, text: str = "", tone: str = "accent", parent=None):
        super().__init__(parent)
        self.setObjectName("connPill")
        self.setFont(pill_font())
        self.set_text(text)
        self.set_tone(tone)

    def set_text(self, text: str) -> None:
        super().setText(text.upper())

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        repolish(self)


class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setObjectName("sectionLabel")
        self.setFont(section_font())


class Dot(QWidget):
    """A small filled circle in a semantic color."""

    def __init__(self, tone: str = "accent", diameter: int = 7, parent=None):
        super().__init__(parent)
        self._tone = tone
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = theme._qcolor(getattr(theme.current(), self._tone))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(0, 0, self._diameter, self._diameter)


class StepRail(QWidget):
    """Three 20px circles + labels joined by 1px rules. States per the
    handoff: completed (filled accent, check), current (accent_soft fill,
    accent border, numeral), future (line border, faint numeral)."""

    LABELS = ("Where", "Credential", "Verify")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = 1
        self.setFixedHeight(28)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def set_current(self, step: int) -> None:
        self.current = step
        self.update()

    def _segments(self) -> list[tuple[str, int, int, int]]:
        """(kind, index, x, width) for circles, labels and rules at the
        current width — pure layout, so tests can assert the run fits."""
        from PySide6.QtGui import QFontMetrics
        circle_d, gap = 20, 9
        widths = []
        for i, label in enumerate(self.LABELS):
            f = QFont(self.font())
            f.setWeight(QFont.Weight.DemiBold if i + 1 == self.current
                        else QFont.Weight.Normal)
            widths.append(QFontMetrics(f).horizontalAdvance(label))
        # the painted run consumes 7 gaps (circle->label x3, label->rule x2,
        # rule->circle x2) -- count them all or the last label clips
        fixed = 3 * circle_d + sum(widths) + 7 * gap
        rule_w = max(12, (self.width() - fixed) // 2)
        segments = []
        x = 0
        for i in range(3):
            segments.append(("circle", i, x, circle_d))
            x += circle_d + gap
            segments.append(("label", i, x, widths[i]))
            x += widths[i]
            if i < 2:
                x += gap
                segments.append(("rule", i, x, rule_w))
                x += rule_w + gap
        return segments

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_font = self.font()
        for kind, index, x, width in self._segments():
            step = index + 1
            if kind == "circle":
                circle_d = width
                cx, cy = x, (self.height() - circle_d) // 2
                if step < self.current:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(theme._qcolor(t.accent))
                    painter.drawEllipse(cx, cy, circle_d, circle_d)
                    painter.setPen(QPen(theme._qcolor(t.accent_ink), 2))
                    painter.drawLine(cx + 6, cy + 10, cx + 9, cy + 13)
                    painter.drawLine(cx + 9, cy + 13, cx + 14, cy + 7)
                elif step == self.current:
                    painter.setBrush(theme._qcolor(t.accent_soft))
                    painter.setPen(QPen(theme._qcolor(t.accent), 1.5))
                    painter.drawEllipse(cx + 1, cy + 1, circle_d - 2, circle_d - 2)
                    painter.setPen(theme._qcolor(t.accent_text))
                    painter.drawText(cx, cy, circle_d, circle_d,
                                     Qt.AlignmentFlag.AlignCenter, str(step))
                else:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(theme._qcolor(t.line), 1.5))
                    painter.drawEllipse(cx + 1, cy + 1, circle_d - 2, circle_d - 2)
                    painter.setPen(theme._qcolor(t.faint))
                    painter.drawText(cx, cy, circle_d, circle_d,
                                     Qt.AlignmentFlag.AlignCenter, str(step))
            elif kind == "label":
                f = QFont(label_font)
                if step == self.current:
                    f.setWeight(QFont.Weight.DemiBold)
                    painter.setPen(theme._qcolor(t.ink))
                elif step < self.current:
                    painter.setPen(theme._qcolor(t.muted))
                else:
                    painter.setPen(theme._qcolor(t.faint))
                painter.setFont(f)
                painter.drawText(x, 0, width, self.height(),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 self.LABELS[index])
            else:  # "rule"
                color = t.accent if step < self.current else t.line
                painter.setPen(QPen(theme._qcolor(color), 1))
                painter.drawLine(x, self.height() // 2, x + width,
                                 self.height() // 2)


class RingSpinner(QWidget):
    """44px ring: 3px track circle with an accent arc, spinning."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(44, 44)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle - 6) % 360
        self.update()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.setPen(QPen(theme._qcolor(t.track), 3))
        painter.drawEllipse(rect)
        painter.setPen(QPen(theme._qcolor(t.accent), 3))
        painter.drawArc(rect, self._angle * 16, 100 * 16)


class _ProbeCircle(QWidget):
    """16px status circle: passed = filled accent + check, running = accent
    border, pending = line border."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "pending"
        self.setFixedSize(16, 16)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.state == "passed":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme._qcolor(t.accent))
            painter.drawEllipse(0, 0, 16, 16)
            painter.setPen(QPen(theme._qcolor(t.accent_ink), 2))
            painter.drawLine(4, 8, 7, 11)
            painter.drawLine(7, 11, 12, 5)
        else:
            color = t.accent if self.state == "running" else t.line
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme._qcolor(color), 1.5))
            painter.drawEllipse(1, 1, 14, 14)


class ProbeList(QWidget):
    """The five preflight probes, client-side paced. The service exposes no
    per-probe signal (and the API is fixed), so rows advance on a timer while
    the single create_profile call is pending; the LAST row must stay
    'running' until the caller resolves it — finish_all() on 200, stop() on
    failure. Rows are never individually marked failed: which probe failed is
    unknown client-side."""

    PROBES = (
        ("List objects", "listing…"),
        ("Read an object", "reading…"),
        ("Write a test object", "writing…"),
        ("Compose slices", "composing…"),
        ("Delete the test object", "deleting…"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("connCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._running = -1
        self._circles: list[_ProbeCircle] = []
        self._names: list[QLabel] = []
        self._details: list[QLabel] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 4, 0, 4)
        column.setSpacing(0)
        for i, (name, verb) in enumerate(self.PROBES):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(15, 11, 15, 11)
            row_layout.setSpacing(11)
            circle = _ProbeCircle()
            name_label = QLabel(name)
            name_label.setObjectName("connBody")
            detail = QLabel("")
            detail.setObjectName("helperText")
            row_layout.addWidget(circle)
            row_layout.addWidget(name_label)
            row_layout.addWidget(detail)
            row_layout.addStretch(1)
            column.addWidget(row)
            self._circles.append(circle)
            self._names.append(name_label)
            self._details.append(detail)

    def states(self) -> list[str]:
        return [c.state for c in self._circles]

    def reset(self) -> None:
        self._timer.stop()
        self._running = -1
        for circle, detail in zip(self._circles, self._details):
            circle.set_state("pending")
            detail.setText("")

    def start(self, interval_ms: int = 900) -> None:
        self.reset()
        self._running = 0
        self._apply()
        self._timer.start(interval_ms)

    def _advance(self) -> None:
        if self._running >= len(self.PROBES) - 1:
            self._timer.stop()      # cap: last probe stays running
            return
        self._circles[self._running].set_state("passed")
        self._details[self._running].setText("")
        self._running += 1
        self._apply()

    def _apply(self) -> None:
        self._circles[self._running].set_state("running")
        self._details[self._running].setText(self.PROBES[self._running][1])

    def stop(self) -> None:
        self._timer.stop()

    def finish_all(self) -> None:
        self._timer.stop()
        for circle, detail in zip(self._circles, self._details):
            circle.set_state("passed")
            detail.setText("")


class _BigCircle(QWidget):
    """24px filled circle: accent+check for verified, danger+'!' for failed."""

    def __init__(self, kind: str = "check", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(24, 24)
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _t) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tone = t.accent if self.kind == "check" else t.danger
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme._qcolor(tone))
        painter.drawEllipse(0, 0, 24, 24)
        painter.setPen(QPen(theme._qcolor(t.accent_ink), 2))
        if self.kind == "check":
            painter.drawLine(6, 12, 10, 16)
            painter.drawLine(10, 16, 18, 8)
        else:
            painter.drawLine(12, 6, 12, 14)
            painter.drawLine(12, 17, 12, 19)


class ConnectionCard(QWidget):
    """One profile as a status card. Client-agnostic: the dialog owns I/O and
    calls the state methods; the card only renders and emits intent."""

    check_clicked = Signal()
    remove_confirmed = Signal()
    show_jobs_clicked = Signal()

    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.setObjectName("connCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        label, tone = AUTH_PRESENTATION.get(
            profile.get("auth_type", ""),
            (str(profile.get("auth_type", "")).upper(), "track"))
        self.name_label = QLabel(profile["name"])
        self.name_label.setObjectName("connName")
        self.pill = Pill(label, tone=tone)
        target = f"gs://{profile['bucket']}/{profile.get('default_prefix') or ''}"
        self.target_label = QLabel(target.rstrip("/"))
        self.target_label.setObjectName("connMono")
        text, line_tone = last_check_line(profile)
        self.check_line = QLabel(text)
        self.check_line.setObjectName(
            "connWarnLine" if line_tone == "warn" else "connMuted")
        self.check_line.setWordWrap(True)

        self.check_button = QPushButton("Check now")
        self.remove_button = QPushButton("Remove")
        for button in (self.check_button, self.remove_button):
            button.setAutoDefault(False)
        self.check_button.clicked.connect(self.check_clicked.emit)
        self.remove_button.clicked.connect(self._show_confirm)

        info = QVBoxLayout()
        info.setSpacing(5)
        name_row = QHBoxLayout()
        name_row.setSpacing(9)
        name_row.addWidget(self.name_label)
        name_row.addWidget(self.pill)
        name_row.addStretch(1)
        info.addLayout(name_row)
        info.addWidget(self.target_label)
        info.addWidget(self.check_line)

        buttons = QVBoxLayout()
        buttons.setSpacing(7)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(15, 13, 15, 13)
        content_layout.setSpacing(11)
        content_layout.addLayout(info, 1)
        content_layout.addLayout(buttons)

        # inline confirm / refusal region — never a QMessageBox over a dialog
        self.region = QWidget()
        self.region.setObjectName("connDangerRegion")
        self.region.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        region_layout = QVBoxLayout(self.region)
        region_layout.setContentsMargins(15, 13, 15, 13)
        region_layout.setSpacing(9)
        head_row = QHBoxLayout()
        head_row.setSpacing(9)
        head_row.addWidget(Dot(tone="danger"),
                           alignment=Qt.AlignmentFlag.AlignTop)
        self.region_text = QLabel("")
        self.region_text.setObjectName("connDangerLine")
        self.region_text.setWordWrap(True)
        head_row.addWidget(self.region_text, 1)
        region_layout.addLayout(head_row)
        self.region_body = QLabel("")
        self.region_body.setObjectName("connDangerLine")
        self.region_body.setWordWrap(True)
        region_layout.addWidget(self.region_body)
        region_buttons = QHBoxLayout()
        region_buttons.setSpacing(9)
        self.confirm_button = QPushButton("Remove")
        self.confirm_button.setObjectName("dangerButton")
        self.show_jobs_button = QPushButton("")
        self.show_jobs_button.setObjectName("dangerButton")
        self.keep_button = QPushButton("Keep it")
        self.keep_button.setObjectName("dangerOutline")
        for button in (self.confirm_button, self.show_jobs_button,
                       self.keep_button):
            button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.remove_confirmed.emit)
        self.show_jobs_button.clicked.connect(self.show_jobs_clicked.emit)
        self.keep_button.clicked.connect(self.reset_region)
        region_buttons.addWidget(self.confirm_button)
        region_buttons.addWidget(self.show_jobs_button)
        region_buttons.addWidget(self.keep_button)
        region_buttons.addStretch(1)
        region_layout.addLayout(region_buttons)
        self.region.hide()

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(content)
        column.addWidget(self.region)

    def _show_confirm(self) -> None:
        self.region_text.setText(
            f"Remove '{self.profile['name']}'? Its saved credential is deleted"
            " with it. This cannot be undone.")
        self.region_body.setText("")
        self.region_body.hide()
        self.confirm_button.show()
        self.show_jobs_button.hide()
        self.region.show()

    def show_refusal(self, n: int) -> None:
        if n == 1:
            self.region_text.setText(
                "This connection is used by 1 job and cannot be deleted while"
                " it exists.")
        else:
            self.region_text.setText(
                f"This connection is used by {n} jobs and cannot be deleted while"
                " they exist.")
        self.region_body.setText(
            "Their reports and bucket paths are read back through it. Delete or"
            " archive those jobs first, or leave this connection in place and"
            " stop using it for new transfers.")
        self.region_body.show()
        self.confirm_button.hide()
        self.show_jobs_button.setText(
            "Show that job" if n == 1 else f"Show those {n} jobs")
        self.show_jobs_button.show()
        self.region.show()

    def reset_region(self) -> None:
        self.region.hide()

    def set_checking(self) -> None:
        self.check_button.setEnabled(False)
        self.remove_button.setEnabled(False)
        self.check_line.setObjectName("connMuted")
        self.check_line.setText("Checking…")
        repolish(self.check_line)

    def _restore_buttons(self) -> None:
        self.check_button.setEnabled(True)
        self.remove_button.setEnabled(True)

    def show_check_summary(self, text: str) -> None:
        self._restore_buttons()
        self.check_line.setObjectName("connMuted")
        self.check_line.setText(f"Checked just now — {text}")
        repolish(self.check_line)

    def show_error_line(self, text: str) -> None:
        self._restore_buttons()
        self.check_line.setObjectName("connDangerLine")
        self.check_line.setText(text)
        repolish(self.check_line)

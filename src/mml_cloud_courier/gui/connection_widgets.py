"""Client-agnostic visual primitives for the connections manager and the
New-connection stepper. QSS-styled parts ride the app-level stylesheet
(theme.qss) and restyle automatically; custom-painted parts read
theme.current() in paintEvent and repaint on theme.notifier.changed via a
bound-method connection, which Qt drops automatically when the widget dies."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

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

    def paintEvent(self, _event) -> None:
        t = theme.current()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_font = self.font()
        metrics_width = self.width()
        circle_d = 20
        y = (self.height() - circle_d) // 2
        gap = 9
        # measure label widths at both weights to lay out three segments
        from PySide6.QtGui import QFontMetrics
        widths = []
        for i, label in enumerate(self.LABELS):
            f = QFont(label_font)
            f.setWeight(QFont.Weight.DemiBold if i + 1 == self.current
                        else QFont.Weight.Normal)
            widths.append(QFontMetrics(f).horizontalAdvance(label))
        fixed = 3 * circle_d + sum(widths) + 6 * gap
        rule_w = max(12, (metrics_width - fixed) // 2)
        x = 0
        for i, label in enumerate(self.LABELS):
            step = i + 1
            # circle
            cx, cy = x, y
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
            x += circle_d + gap
            # label
            f = QFont(label_font)
            if step == self.current:
                f.setWeight(QFont.Weight.DemiBold)
                painter.setPen(theme._qcolor(t.ink))
            elif step < self.current:
                painter.setPen(theme._qcolor(t.muted))
            else:
                painter.setPen(theme._qcolor(t.faint))
            painter.setFont(f)
            painter.drawText(x, 0, widths[i], self.height(),
                             Qt.AlignmentFlag.AlignVCenter, label)
            x += widths[i] + gap
            # rule to the next circle
            if i < 2:
                color = t.accent if step < self.current else t.line
                painter.setPen(QPen(theme._qcolor(color), 1))
                painter.drawLine(x, self.height() // 2, x + rule_w,
                                 self.height() // 2)
                x += rule_w + gap


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

"""Reusable visual primitives for the 11vatedTech intelligence interface."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.ui.theme import (
    COLORS,
    color,
    display_font,
    font,
    mono_family,
    text_family,
    with_alpha,
)

TONE_OBJECT = {
    "cyan": "Pill",
    "green": "PillGreen",
    "amber": "PillAmber",
    "red": "PillRed",
    "violet": "PillViolet",
    "muted": "PillMuted",
    "blue": "Pill",
}


def label(
    text: str,
    role: str = "Body",
    parent: QWidget | None = None,
    *,
    wrap: bool = False,
) -> QLabel:
    item = QLabel(text, parent)
    item.setObjectName(role)
    item.setWordWrap(wrap)
    return item


def button(
    text: str,
    *,
    variant: str = "",
    parent: QWidget | None = None,
    on_click: Callable[[], None] | None = None,
    tooltip: str = "",
) -> QPushButton:
    item = QPushButton(text, parent)
    if variant:
        item.setObjectName(variant)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        item.setToolTip(tooltip)
    if on_click is not None:
        item.clicked.connect(on_click)
    return item


def divider(parent: QWidget | None = None) -> QFrame:
    item = QFrame(parent)
    item.setObjectName("Divider")
    item.setFixedHeight(1)
    return item


def hstack(
    *items: QWidget,
    spacing: int = 10,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    for item in items:
        layout.addWidget(item)
    return widget


class Surface(QFrame):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        raised: bool = False,
        inset: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PanelRaised" if raised else "Inset" if inset else "Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class Card(Surface):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        raised: bool = False,
        inset: bool = False,
        margins: tuple[int, int, int, int] = (18, 16, 18, 16),
        spacing: int = 10,
    ) -> None:
        super().__init__(parent, raised=raised, inset=inset)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)


class Pill(QLabel):
    def __init__(self, text: str, tone: str = "cyan", parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName(TONE_OBJECT.get(tone, "Pill"))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.setObjectName(TONE_OBJECT.get(tone, "Pill"))
        self.style().unpolish(self)
        self.style().polish(self)


class StatusDot(QWidget):
    def __init__(self, tone: str = "muted", size: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = tone
        self.setFixedSize(size + 6, size + 6)

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        base = color(self._tone)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(base.name(), 36))
        painter.drawEllipse(center, self.width() / 2, self.height() / 2)
        painter.setBrush(base)
        painter.drawEllipse(center, self.width() / 2 - 4, self.height() / 2 - 4)
        painter.end()


class LogoMark(QWidget):
    def __init__(self, size: int = 34, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        center = rect.center()
        radius = min(rect.width(), rect.height()) / 2
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(with_alpha(COLORS["cyan"], 210), 1.4))
        painter.drawEllipse(center, radius * 0.86, radius * 0.86)
        painter.setPen(QPen(with_alpha(COLORS["blue"], 150), 1))
        painter.drawEllipse(center, radius * 0.58, radius * 0.58)
        painter.setPen(QPen(with_alpha(COLORS["ice"], 210), 1.5))
        painter.drawLine(QPointF(center.x(), center.y() - radius * 0.5),
                         QPointF(center.x(), center.y() + radius * 0.5))
        painter.drawLine(QPointF(center.x() - radius * 0.5, center.y()),
                         QPointF(center.x() + radius * 0.5, center.y()))
        painter.setBrush(with_alpha(COLORS["cyan"], 230))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius * 0.13, radius * 0.13)
        painter.end()


class NavButton(QToolButton):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setObjectName("Nav")
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class GroupLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.setObjectName("Faint")
        self.setContentsMargins(10, 14, 4, 4)


class PageHeader(QWidget):
    def __init__(
        self,
        eyebrow: str,
        title: str,
        subtitle: str,
        *,
        actions: Sequence[QWidget] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(18)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        eyebrow_label = label(eyebrow.upper(), "Eyebrow")
        title_label = label(title, "PageTitle")
        subtitle_label = label(subtitle, "PageSubtitle", wrap=True)
        copy.addWidget(eyebrow_label)
        copy.addWidget(title_label)
        copy.addWidget(subtitle_label)
        layout.addLayout(copy, 1)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        for action in actions:
            action_row.addWidget(action)
        layout.addLayout(action_row)


class SectionHeader(QWidget):
    def __init__(
        self,
        title: str,
        meta: str = "",
        *,
        actions: Sequence[QWidget] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        copy = QVBoxLayout()
        copy.setSpacing(1)
        copy.addWidget(label(title, "SectionTitle"))
        if meta:
            copy.addWidget(label(meta, "SectionMeta"))
        layout.addLayout(copy, 1)
        for action in actions:
            layout.addWidget(action)


class MetricTile(Card):
    def __init__(
        self,
        label_text: str,
        value: str = "—",
        detail: str = "",
        *,
        tone: str = "cyan",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, margins=(14, 12, 14, 12), spacing=3)
        self.setMinimumHeight(92)
        top = QHBoxLayout()
        top.setSpacing(5)
        self.label = label(label_text.upper(), "MetricLabel")
        top.addWidget(self.label, 1)
        self.dot = StatusDot(tone, 6)
        top.addWidget(self.dot)
        self.body.addLayout(top)
        self.value = label(value, "MetricValueSmall")
        self.body.addWidget(self.value)
        self.detail = label(detail, "Faint", wrap=True)
        self.body.addWidget(self.detail)

    def set_value(self, value: str, detail: str | None = None, tone: str | None = None) -> None:
        self.value.setText(value)
        if detail is not None:
            self.detail.setText(detail)
        if tone is not None:
            self.dot.set_tone(tone)


class RingGauge(QWidget):
    def __init__(
        self,
        label_text: str,
        *,
        tone: str = "cyan",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label_text
        self._value: float | None = None
        self._unit = "%"
        self._tone = tone
        self._detail = ""
        self.setMinimumSize(122, 122)

    def set_value(self, value: float | None, unit: str = "%", detail: str = "") -> None:
        self._value = max(0.0, min(100.0, float(value))) if value is not None else None
        self._unit = unit
        self._detail = detail
        self.update()

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 8
        box = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        ring = box.adjusted(side * 0.12, side * 0.12, -side * 0.12, -side * 0.12)
        base = color(self._tone)
        pen = QPen(with_alpha(COLORS["border"], 230), 5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(ring, 225 * 16, -270 * 16)
        if self._value is not None:
            pen.setColor(base)
            pen.setWidth(5)
            painter.setPen(pen)
            painter.drawArc(ring, 225 * 16, int(-270 * 16 * self._value / 100))
        painter.setFont(font(10, QFont.Weight.DemiBold))
        painter.setPen(with_alpha(COLORS["muted"], 240))
        painter.drawText(
            QRectF(box.left(), box.top() + side * 0.18, side, 18),
            Qt.AlignmentFlag.AlignHCenter,
            self._label.upper(),
        )
        value_text = "—" if self._value is None else f"{self._value:.0f}{self._unit}"
        painter.setFont(display_font(23, QFont.Weight.DemiBold))
        painter.setPen(with_alpha(COLORS["ice"], 245))
        painter.drawText(
            QRectF(box.left(), box.center().y() - 20, side, 38),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            value_text,
        )
        if self._detail:
            painter.setFont(font(9))
            painter.setPen(with_alpha(COLORS["muted"], 220))
            painter.drawText(
                QRectF(box.left(), box.center().y() + 20, side, 28),
                Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                self._detail,
            )
        painter.end()


class Sparkline(QWidget):
    def __init__(self, *, tone: str = "cyan", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._tone = tone
        self.setMinimumHeight(36)

    def set_values(self, values: Sequence[float]) -> None:
        self._values = [float(item) for item in values][-48:]
        self.update()

    def paintEvent(self, _event: object) -> None:
        if len(self._values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = 3.0
        low = min(self._values)
        high = max(self._values)
        span = max(0.001, high - low)
        points = []
        for index, value in enumerate(self._values):
            x = margin + index / (len(self._values) - 1) * (self.width() - margin * 2)
            y = self.height() - margin - (value - low) / span * (self.height() - margin * 2)
            points.append(QPointF(x, y))
        path = QPen(color(self._tone), 1.7)
        path.setCapStyle(Qt.PenCapStyle.RoundCap)
        path.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(path)
        for index in range(1, len(points)):
            painter.drawLine(points[index - 1], points[index])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(with_alpha(color(self._tone).name(), 45))
        painter.drawEllipse(points[-1], 3, 3)
        painter.end()


class AutoTextBrowser(QTextBrowser):
    """A borderless rich-text view that grows to fit its document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)

    def set_html(self, value: str) -> None:
        self.setHtml(value)
        self._fit()

    def _fit(self, *_args: object) -> None:
        document_height = self.document().size().height()
        target = max(28, min(900, int(document_height + 8)))
        self.setFixedHeight(target)


class EmptyState(QWidget):
    def __init__(
        self,
        title: str,
        copy: str,
        *,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 30, 18, 30)
        layout.setSpacing(7)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark = QLabel("◇")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(
            f"color:{COLORS['cyan_dim']};font-size:27px;font-family:'{text_family()}';"
        )
        layout.addWidget(mark)
        heading = label(title, "SectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        description = label(copy, "Muted", wrap=True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setMaximumWidth(420)
        layout.addWidget(description, alignment=Qt.AlignmentFlag.AlignCenter)
        if action is not None:
            layout.addSpacing(4)
            layout.addWidget(action, alignment=Qt.AlignmentFlag.AlignCenter)


class KeyValue(QWidget):
    def __init__(self, key: str, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(10)
        key_label = label(key, "Faint")
        key_label.setMinimumWidth(92)
        self.value = label(value, "Mono", wrap=True)
        layout.addWidget(key_label)
        layout.addWidget(self.value, 1)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class SearchField(QWidget):
    text_changed = Signal(str)

    def __init__(self, placeholder: str = "Search", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QLineEdit

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.input = QLineEdit()
        self.input.setObjectName("Search")
        self.input.setPlaceholderText(placeholder)
        self.input.textChanged.connect(self.text_changed)
        layout.addWidget(self.input)


def mono_label(text: str, parent: QWidget | None = None) -> QLabel:
    value = QLabel(text, parent)
    value.setObjectName("Mono")
    value.setStyleSheet(f"font-family:'{mono_family()}';")
    return value

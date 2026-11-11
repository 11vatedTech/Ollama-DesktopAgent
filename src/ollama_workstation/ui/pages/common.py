"""Small helpers shared by the redesigned workspace pages."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, QThreadPool
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.ui.components import Card, Pill, label
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.theme import COLORS, font
from ollama_workstation.ui.workers import FunctionWorker


def format_bytes(value: Any, *, precision: int = 1) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "—"
    if amount < 0:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    return f"{amount:.{precision}f} {units[index]}"


def format_timestamp(value: Any, *, short: bool = False) -> str:
    text = str(value or "")
    if not text:
        return "—"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%H:%M" if short else "%d %b · %H:%M")
    except ValueError:
        return text[:16]


def status_tone(status: str) -> str:
    normalized = status.casefold()
    if normalized in {"completed", "complete", "passed", "ready", "online", "valid", "success"}:
        return "green"
    if normalized in {"failed", "error", "corrupt", "invalid", "offline", "blocked"}:
        return "red"
    if normalized in {"running", "thinking", "planning", "executing"}:
        return "cyan"
    if normalized in {"verification_failed", "needs_review", "review", "waiting", "pending",
                      "timed_out", "exhausted", "approval"}:
        return "amber"
    if normalized in {"cancelled", "stale", "superseded", "rejected"}:
        return "muted"
    return "muted"


def status_pill(text: str, tone: str | None = None) -> Pill:
    return Pill(text.replace("_", " "), tone or status_tone(text))


def short_path(value: Any, limit: int = 46) -> str:
    """Display the identifying tail of a path without breaking it mid-token."""
    text = str(value or "").strip()
    if not text:
        return "—"
    if len(text) <= limit:
        return text
    path = Path(text)
    parts = [part for part in path.parts if part not in (path.anchor, path.drive)]
    tail = "\\".join(parts[-2:]) if len(parts) >= 2 else path.name
    candidate = f"…\\{tail}"
    if len(candidate) <= limit:
        return candidate
    return f"…\\{path.name or tail}"[:limit]


class TwoLineDelegate(QStyledItemDelegate):
    """Paint stable two-line rows; native selected-item painting flattens newlines."""

    def __init__(self, parent: QWidget, row_height: int = 50) -> None:
        super().__init__(parent)
        self.row_height = row_height

    def sizeHint(self, option: QStyleOptionViewItem, index: object) -> QSize:
        return QSize(max(80, option.rect.width()), self.row_height)

    def paint(
        self,
        painter: object,
        option: QStyleOptionViewItem,
        index: object,
    ) -> None:
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)  # type: ignore[arg-type]
        text = view_option.text
        view_option.text = ""
        style = view_option.widget.style() if view_option.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, view_option, painter, view_option.widget)
        lines = text.split("\n", 1)
        selected = bool(view_option.state & QStyle.StateFlag.State_Selected)
        brush = index.data(Qt.ItemDataRole.ForegroundRole)  # type: ignore[attr-defined]
        first_color = QColor(COLORS["ice"]) if selected else (
            brush.color() if isinstance(brush, QBrush) else QColor(COLORS["text_secondary"])
        )
        second_color = QColor(COLORS["text_secondary"] if selected else COLORS["muted"])
        inner = view_option.rect.adjusted(8, 4, -8, -4)
        line_height = max(18, inner.height() // max(1, len(lines)))
        painter.save()  # type: ignore[attr-defined]
        painter.setFont(font(11))  # type: ignore[attr-defined]
        metrics = painter.fontMetrics()  # type: ignore[attr-defined]
        painter.setPen(first_color)  # type: ignore[attr-defined]
        first = metrics.elidedText(lines[0], Qt.TextElideMode.ElideRight, inner.width())
        painter.drawText(  # type: ignore[attr-defined]
            QRectF(inner.left(), inner.top(), inner.width(), line_height),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            first,
        )
        if len(lines) > 1:
            painter.setFont(font(10))  # type: ignore[attr-defined]
            second_metrics = painter.fontMetrics()  # type: ignore[attr-defined]
            painter.setPen(second_color)  # type: ignore[attr-defined]
            second = second_metrics.elidedText(
                lines[1], Qt.TextElideMode.ElideRight, inner.width()
            )
            painter.drawText(  # type: ignore[attr-defined]
                QRectF(inner.left(), inner.top() + line_height, inner.width(), line_height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                second,
            )
        painter.restore()  # type: ignore[attr-defined]


def prepare_list(
    widget: QListWidget,
    *,
    spacing: int = 2,
    row_height: int | None = None,
) -> None:
    """Make multi-line list rows readable instead of silently clipping them."""
    widget.setWordWrap(False)
    widget.setTextElideMode(Qt.TextElideMode.ElideRight)
    widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    widget.setUniformItemSizes(False)
    widget.setSpacing(spacing)
    if row_height is not None:
        widget.setItemDelegate(TwoLineDelegate(widget, row_height))


def two_line_item(
    text: str,
    *,
    height: int = 50,
    data: Any = None,
) -> QListWidgetItem:
    item = QListWidgetItem(text)
    item.setSizeHint(QSize(0, height))
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    return item


def make_scroll(content: QWidget, *, spacing: int = 16, margins: tuple[int, int, int, int] = (0, 0, 8, 24)) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    wrapper = QWidget()
    wrapper.setObjectName("PageBody")
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    layout.addWidget(content)
    layout.addStretch(1)
    scroll.setWidget(wrapper)
    return scroll


def content_column(spacing: int = 16) -> tuple[QWidget, QVBoxLayout]:
    widget = QWidget()
    widget.setObjectName("PageBody")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    return widget, layout


def panel_header(title: str, meta: str = "", actions: list[QWidget] | None = None) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    copy = QVBoxLayout()
    copy.setSpacing(1)
    copy.addWidget(label(title, "SectionTitle"))
    if meta:
        copy.addWidget(label(meta, "SectionMeta"))
    layout.addLayout(copy, 1)
    for action in actions or []:
        layout.addWidget(action)
    return row


def empty_panel(
    title: str,
    copy: str,
    action: QWidget | None = None,
    parent: QWidget | None = None,
) -> Card:
    card = Card(parent, margins=(18, 22, 18, 22))
    card.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    heading = label(title, "SectionTitle")
    heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
    body = label(copy, "Muted", wrap=True)
    body.setAlignment(Qt.AlignmentFlag.AlignCenter)
    card.body.addWidget(heading)
    card.body.addWidget(body)
    if action is not None:
        card.body.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(action)
        row.addStretch(1)
        card.body.addLayout(row)
    return card


def start_worker(
    context: AppContext,
    function: Callable[..., Any],
    *,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    on_result: Callable[[Any], None],
    on_error: Callable[[str], None],
    on_finished: Callable[[], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> FunctionWorker:
    worker = FunctionWorker(function, *args, **(kwargs or {}))
    worker.signals.result.connect(on_result)
    worker.signals.error.connect(on_error)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    context.pool.start(worker)
    return worker


def start_background(
    pool: QThreadPool,
    function: Callable[..., Any],
    *args: Any,
    on_result: Callable[[Any], None],
    on_error: Callable[[str], None],
    on_finished: Callable[[], None] | None = None,
    on_progress: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> FunctionWorker:
    worker = FunctionWorker(function, *args, **kwargs)
    worker.signals.result.connect(on_result)
    worker.signals.error.connect(on_error)
    if on_progress is not None:
        worker.signals.progress.connect(on_progress)
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    pool.start(worker)
    return worker


def muted_caption(text: str) -> QLabel:
    item = label(text, "Faint", wrap=True)
    item.setStyleSheet(f"color:{COLORS['faint']};")
    return item


def local_now() -> datetime:
    return datetime.now(UTC).astimezone()

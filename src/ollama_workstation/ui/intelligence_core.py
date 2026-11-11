"""Reusable animated intelligence core.

Every visual state is driven by real workstation activity. The widget never
invents reasoning progress: callers set a state at a runtime event boundary.
Rendering is local QPainter work, paced at 60 FPS when motion is enabled, with
reduced-motion and compact modes for accessibility and constrained layouts.
"""

from __future__ import annotations

import math
import random
from enum import StrEnum

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ollama_workstation.ui.theme import COLORS, color, with_alpha


class IntelligenceState(StrEnum):
    STANDBY = "standby"
    LISTENING = "listening"
    THINKING = "thinking"
    PLANNING = "planning"
    ROUTING = "routing"
    EXECUTING = "executing"
    DELEGATING = "delegating"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETE = "complete"
    WARNING = "warning"


STATE_META: dict[IntelligenceState, tuple[str, str, str]] = {
    IntelligenceState.STANDBY: ("STANDBY", "Local intelligence ready", COLORS["cyan"]),
    IntelligenceState.LISTENING: ("LISTENING", "Microphone level active", COLORS["blue"]),
    IntelligenceState.THINKING: ("THINKING", "Reasoning through the request", COLORS["cyan"]),
    IntelligenceState.PLANNING: ("PLANNING", "Structuring the task", COLORS["violet"]),
    IntelligenceState.ROUTING: ("ROUTING", "Selecting local intelligence", COLORS["blue"]),
    IntelligenceState.EXECUTING: ("EXECUTING", "Tool and model activity", COLORS["cyan"]),
    IntelligenceState.DELEGATING: ("DELEGATING", "Coordinating specialist agents", COLORS["violet"]),
    IntelligenceState.AWAITING_APPROVAL: (
        "AWAITING APPROVAL",
        "Human decision required",
        COLORS["amber"],
    ),
    IntelligenceState.COMPLETE: ("COMPLETE", "Verified activity finished", COLORS["green"]),
    IntelligenceState.WARNING: ("ATTENTION", "A condition needs review", COLORS["red"]),
}


def coerce_state(value: str | IntelligenceState) -> IntelligenceState:
    try:
        return value if isinstance(value, IntelligenceState) else IntelligenceState(value)
    except ValueError:
        return IntelligenceState.STANDBY


class IntelligenceCore(QWidget):
    """Layered holographic intelligence visualization with real state semantics."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
        caption: bool = True,
    ) -> None:
        super().__init__(parent)
        self._state = IntelligenceState.STANDBY
        self._detail = ""
        self._phase = 0.0
        self._audio_level = 0.0
        self._reduced_motion = False
        self._intensity = 1.0
        self._compact = compact
        self._caption = caption
        self._hover = False
        self.setMouseTracking(True)
        self.setAccessibleName("Intelligence core")
        self.setAccessibleDescription(
            "Animated operational state for the local intelligence workstation. "
            f"Current state: {self._state.value}."
        )
        self.setMinimumSize(150, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._reseed()
        self._apply_timer()

    def _reseed(self) -> None:
        generator = random.Random(0x11A7ED)
        self._particles = [
            (
                generator.uniform(0.56, 0.95),
                generator.uniform(0, math.tau),
                generator.uniform(0.10, 0.55),
                generator.uniform(0.8, 2.3),
                generator.uniform(0.18, 0.9),
            )
            for _ in range(76)
        ]
        self._ticks = [
            (generator.uniform(0.62, 1.0), generator.uniform(0, math.tau), generator.uniform(3, 14))
            for _ in range(28)
        ]

    def set_state(
        self,
        state: str | IntelligenceState,
        detail: str = "",
        *,
        pulse: bool = True,
    ) -> None:
        next_state = coerce_state(state)
        changed = next_state is not self._state
        self._state = next_state
        self._detail = detail.strip()
        self.setAccessibleDescription(
            "Animated operational state for the local intelligence workstation. "
            f"Current state: {next_state.value}."
            + (f" {self._detail}" if self._detail else "")
        )
        if changed and pulse and not self._reduced_motion:
            self._phase += 0.55
        self.update()

    @property
    def state(self) -> IntelligenceState:
        return self._state

    def set_audio_level(self, level: float) -> None:
        self._audio_level = max(0.0, min(1.0, float(level)))
        if self._state is IntelligenceState.LISTENING:
            self.update()

    def set_motion(self, *, reduced: bool, intensity: float = 1.0) -> None:
        self._reduced_motion = bool(reduced)
        self._intensity = max(0.2, min(1.0, float(intensity)))
        self._apply_timer()
        self.update()

    def set_compact(self, compact: bool) -> None:
        self._compact = bool(compact)
        self.updateGeometry()
        self.update()

    def _apply_timer(self) -> None:
        if self._reduced_motion:
            self._timer.setInterval(750)
        else:
            self._timer.setInterval(16)
        if not self._timer.isActive():
            self._timer.start()

    def _advance(self) -> None:
        speed = 0.012 if self._reduced_motion else 0.026 + 0.05 * self._intensity
        if self._state in {
            IntelligenceState.THINKING,
            IntelligenceState.PLANNING,
            IntelligenceState.ROUTING,
        }:
            speed *= 1.35
        elif self._state is IntelligenceState.DELEGATING:
            speed *= 1.15
        elif self._state in {IntelligenceState.COMPLETE, IntelligenceState.WARNING}:
            speed *= 0.55
        self._phase = (self._phase + speed) % math.tau
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(220, 220) if self._compact else QSize(410, 410)

    def minimumSizeHint(self) -> QSize:
        return QSize(132, 132) if self._compact else QSize(210, 210)

    def enterEvent(self, event: object) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        rect = QRectF(self.rect())
        painter.fillRect(rect, Qt.GlobalColor.transparent)
        side = min(rect.width(), rect.height())
        if side < 24:
            return
        label, default_detail, accent = STATE_META[self._state]
        accent_color = color(accent)
        center = QPointF(rect.center().x(), rect.center().y())
        if self._caption:
            center.setY(center.y() - min(30.0, side * 0.09))
        radius = side * (0.29 if self._compact else 0.29)
        phase = self._phase
        intensity = 1.0 if self._reduced_motion else self._intensity
        self._draw_aura(painter, center, radius, accent_color)
        self._draw_orbits(painter, center, radius, accent_color, phase, intensity)
        self._draw_ticks(painter, center, radius, accent_color, phase)
        self._draw_particles(painter, center, radius, accent_color, phase, intensity)
        if self._state is IntelligenceState.DELEGATING:
            self._draw_delegation(painter, center, radius, accent_color, phase)
        if self._state is IntelligenceState.LISTENING:
            self._draw_waveform(painter, center, radius, accent_color, phase)
        self._draw_sphere(painter, center, radius, accent_color, phase)
        if self._state in {IntelligenceState.EXECUTING, IntelligenceState.THINKING}:
            self._draw_energy_pulses(painter, center, radius, accent_color, phase)
        if self._state is IntelligenceState.AWAITING_APPROVAL:
            self._draw_approval_marker(painter, center, radius, accent_color, phase)
        if self._state is IntelligenceState.COMPLETE:
            self._draw_completion_marker(painter, center, radius, accent_color)
        if self._state is IntelligenceState.WARNING:
            self._draw_warning_marker(painter, center, radius, accent_color)
        if self._caption:
            self._draw_caption(painter, rect, center, radius, label, accent_color)
        painter.end()

    def _draw_aura(
        self, painter: QPainter, center: QPointF, radius: float, accent: QColor
    ) -> None:
        glow_radius = radius * (2.2 if self._hover else 2.0)
        gradient = QRadialGradient(center, glow_radius)
        gradient.setColorAt(0.0, with_alpha(accent.name(), 42))
        gradient.setColorAt(0.45, with_alpha(accent.name(), 15))
        gradient.setColorAt(1.0, with_alpha(accent.name(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, glow_radius, glow_radius)

    def _draw_orbits(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
        intensity: float,
    ) -> None:
        painter.save()
        painter.translate(center)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rings = (
            (1.72, 0.26, 3.0, 1.0),
            (1.46, -0.10, 5.0, 0.72),
            (1.20, 0.12, 8.0, 0.46),
        )
        for index, (scale, drift, width, opacity) in enumerate(rings):
            painter.save()
            painter.rotate(math.degrees(phase * (0.35 if index != 1 else -0.24) + drift))
            box = QRectF(-radius * scale, -radius * scale, radius * 2 * scale, radius * 2 * scale)
            pen = QPen(with_alpha(accent.name(), int(120 * opacity)), width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            span = 112 if index == 0 else 74
            start = 20 + index * 53
            painter.drawArc(box, int(start * 16), int(span * 16))
            painter.setPen(QPen(with_alpha(accent.name(), int(35 * opacity)), 1))
            painter.drawArc(box, int((start + 148) * 16), int((span + 20) * 16))
            if index == 0:
                painter.drawEllipse(box)
            painter.restore()
        painter.setPen(QPen(with_alpha(accent.name(), 34), 1, Qt.PenStyle.DashLine))
        painter.drawEllipse(QPointF(0, 0), radius * 1.58, radius * 1.58)
        painter.restore()
        del intensity

    def _draw_ticks(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
    ) -> None:
        painter.save()
        painter.translate(center)
        for index, (scale, angle, length) in enumerate(self._ticks):
            rotation = angle + phase * (0.35 if index % 3 else -0.24)
            painter.save()
            painter.rotate(math.degrees(rotation))
            alpha = 32 + (index % 5) * 15
            painter.setPen(QPen(with_alpha(accent.name(), alpha), 1))
            outer = radius * (scale + 0.025)
            inner = outer - length
            painter.drawLine(QPointF(0, -outer), QPointF(0, -inner))
            painter.restore()
        painter.restore()

    def _draw_particles(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
        intensity: float,
    ) -> None:
        painter.save()
        painter.translate(center)
        for orbit, angle, speed, size, brightness in self._particles:
            current = angle + phase * speed * (0.5 + intensity)
            x = math.cos(current) * radius * orbit
            y = math.sin(current) * radius * orbit * 0.66
            alpha = int(45 + 105 * brightness)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(with_alpha(accent.name(), alpha))
            painter.drawEllipse(QPointF(x, y), size, size)
        painter.restore()

    def _draw_delegation(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
    ) -> None:
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        nodes = 5
        for index in range(nodes):
            angle = phase * 0.6 + index * math.tau / nodes
            orbit = radius * (1.23 + 0.08 * math.sin(phase + index))
            point = QPointF(center.x() + math.cos(angle) * orbit,
                            center.y() + math.sin(angle) * orbit * 0.72)
            pen = QPen(with_alpha(accent.name(), 45 + index * 12), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(center, point)
            painter.setPen(QPen(with_alpha(accent.name(), 140), 2))
            painter.drawEllipse(point, 3.4, 3.4)
            painter.setBrush(with_alpha(accent.name(), 22))
            painter.drawEllipse(point, 10 + 2 * math.sin(phase + index), 10 + 2 * math.sin(phase + index))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.restore()

    def _draw_waveform(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
    ) -> None:
        painter.save()
        painter.translate(center)
        painter.setPen(QPen(with_alpha(accent.name(), 120), 1.4))
        points = 72
        for index in range(points):
            angle = index / points * math.tau
            level = 0.12 + self._audio_level * 0.7
            amplitude = radius * level * (0.45 + 0.55 * abs(math.sin(angle * 3 + phase * 4)))
            inner = radius * 1.06
            outer = inner + amplitude
            painter.drawLine(
                QPointF(math.cos(angle) * inner, math.sin(angle) * inner),
                QPointF(math.cos(angle) * outer, math.sin(angle) * outer),
            )
        painter.restore()

    def _draw_sphere(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
    ) -> None:
        gradient = QRadialGradient(
            QPointF(center.x() - radius * 0.22, center.y() - radius * 0.28),
            radius * 1.35,
        )
        gradient.setColorAt(0.0, with_alpha("#ffffff", 240))
        gradient.setColorAt(0.12, with_alpha(accent.name(), 225))
        gradient.setColorAt(0.46, with_alpha(accent.darker(150).name(), 150))
        gradient.setColorAt(0.82, with_alpha("#07131f", 220))
        gradient.setColorAt(1.0, with_alpha("#02060b", 250))
        painter.setPen(QPen(with_alpha(accent.name(), 165), 1.3))
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius * 0.69, radius * 0.69)

        painter.save()
        painter.translate(center)
        clip = QPainterPath()
        clip.addEllipse(QPointF(0, 0), radius * 0.67, radius * 0.67)
        painter.setClipPath(clip)
        for index in range(5):
            offset = (index - 2) * radius * 0.19
            painter.setPen(QPen(with_alpha(accent.name(), 38 + index * 10), 1))
            painter.drawEllipse(QPointF(0, offset * 0.45), radius * 0.58, radius * 0.19)
        painter.setPen(QPen(with_alpha("#ffffff", 54), 0.8))
        for index in range(7):
            angle = phase * 0.35 + index * math.tau / 7
            painter.drawLine(
                QPointF(math.cos(angle) * radius * 0.12, math.sin(angle) * radius * 0.12),
                QPointF(math.cos(angle) * radius * 0.56, math.sin(angle) * radius * 0.56),
            )
        painter.restore()

        conical = QConicalGradient(center, math.degrees(phase))
        conical.setColorAt(0.0, with_alpha(accent.name(), 0))
        conical.setColorAt(0.22, with_alpha(accent.lighter(130).name(), 120))
        conical.setColorAt(0.35, with_alpha("#ffffff", 0))
        conical.setColorAt(0.72, with_alpha(accent.name(), 80))
        conical.setColorAt(1.0, with_alpha(accent.name(), 0))
        painter.setBrush(conical)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius * 0.65, radius * 0.65)

    def _draw_energy_pulses(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
    ) -> None:
        painter.save()
        painter.translate(center)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in range(2):
            progress = ((phase * 0.22 + index * 0.5) % 1.0)
            size = radius * (0.73 + progress * 1.2)
            alpha = int(115 * (1.0 - progress))
            painter.setPen(QPen(with_alpha(accent.name(), alpha), 1.2))
            painter.drawEllipse(QPointF(0, 0), size, size * 0.72)
        painter.restore()

    def _draw_approval_marker(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
        phase: float,
    ) -> None:
        pulse = 0.5 + 0.5 * math.sin(phase * 1.7)
        painter.save()
        painter.translate(center)
        painter.setPen(QPen(with_alpha(accent.name(), int(80 + pulse * 100)), 2))
        painter.setBrush(with_alpha(accent.name(), int(20 + pulse * 18)))
        box = QRectF(-radius * 0.10, -radius * 0.10, radius * 0.20, radius * 0.20)
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(QPen(with_alpha("#fff5df", 210), 2))
        painter.drawLine(QPointF(0, radius * 0.035), QPointF(0, -radius * 0.015))
        painter.drawPoint(QPointF(0, -radius * 0.045))
        painter.restore()

    def _draw_completion_marker(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
    ) -> None:
        painter.save()
        painter.translate(center)
        pen = QPen(with_alpha(accent.name(), 235), max(2.0, radius * 0.026))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        path = QPainterPath(QPointF(-radius * 0.13, 0))
        path.lineTo(-radius * 0.025, radius * 0.10)
        path.lineTo(radius * 0.16, -radius * 0.11)
        painter.drawPath(path)
        painter.restore()

    def _draw_warning_marker(
        self,
        painter: QPainter,
        center: QPointF,
        radius: float,
        accent: QColor,
    ) -> None:
        painter.save()
        painter.translate(center)
        painter.setPen(QPen(with_alpha(accent.name(), 235), max(2.0, radius * 0.026)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(0, -radius * 0.11), radius * 0.028, radius * 0.028)
        painter.drawLine(QPointF(0, -radius * 0.015), QPointF(0, radius * 0.12))
        painter.restore()

    def _draw_caption(
        self,
        painter: QPainter,
        rect: QRectF,
        center: QPointF,
        radius: float,
        label: str,
        accent: QColor,
    ) -> None:
        top = center.y() + radius * 1.47
        if top + 16 > rect.bottom():
            return
        state_font = QFont(display_family_for_caption())
        state_font.setPixelSize(9 if self._compact else 10)
        state_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(state_font)
        painter.setPen(with_alpha(accent.name(), 200))
        painter.drawText(
            QRectF(rect.left(), top, rect.width(), 16),
            Qt.AlignmentFlag.AlignHCenter,
            label,
        )
        if self._compact or not self._detail:
            return
        detail_top = top + 21
        if detail_top + 20 > rect.bottom():
            return
        detail_font = QFont(display_family_for_caption())
        detail_font.setPixelSize(11)
        detail_font.setWeight(QFont.Weight.Normal)
        painter.setFont(detail_font)
        painter.setPen(with_alpha(COLORS["muted"], 220))
        painter.drawText(
            QRectF(rect.left() + 12, detail_top, rect.width() - 24, 22),
            Qt.AlignmentFlag.AlignHCenter,
            self._detail,
        )


def display_family_for_caption() -> str:
    from ollama_workstation.ui.theme import display_family

    return display_family()

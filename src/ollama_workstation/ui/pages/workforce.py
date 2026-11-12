"""Workforce Studio: compose, inspect, approve and supervise a local agent team."""

from __future__ import annotations

import html
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.orchestration import (
    AgentAssignment,
    WorkforceOrchestrator,
    WorkforcePlan,
    WorkforcePlanner,
)
from ollama_workstation.agents.process_identity import current_process_identity
from ollama_workstation.agents.workforce import WorkforceMatcher
from ollama_workstation.config import save_settings
from ollama_workstation.models.identity import ROLE_DEFINITIONS
from ollama_workstation.ui.components import (
    Card,
    PageHeader,
    Pill,
    SectionHeader,
    button,
    divider,
    label,
)
from ollama_workstation.ui.formatting import render_compact
from ollama_workstation.ui.intelligence_core import IntelligenceCore
from ollama_workstation.ui.pages.common import (
    format_timestamp,
    prepare_list,
    short_path,
    two_line_item,
)
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.theme import COLORS, font, with_alpha
from ollama_workstation.ui.workers import FunctionWorker

TASK_TONES = {
    "pending": COLORS["faint"],
    "running": COLORS["cyan"],
    "completed": COLORS["green"],
    "failed": COLORS["red"],
    "blocked": COLORS["amber"],
    "cancelled": COLORS["muted"],
    "timed_out": COLORS["amber"],
    "rejected": COLORS["muted"],
    "verification_failed": COLORS["red"],
}


class WorkforceGraph(QWidget):
    node_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan: WorkforcePlan | None = None
        self.statuses: dict[str, str] = {}
        self._nodes: dict[str, QRectF] = {}
        self.selected = ""
        self.setMinimumHeight(270)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_plan(self, plan: WorkforcePlan | None, statuses: dict[str, str] | None = None) -> None:
        self.plan = plan
        self.statuses = dict(statuses or {})
        self._nodes = {}
        self.update()

    def set_statuses(self, statuses: dict[str, str]) -> None:
        self.statuses.update(statuses)
        self.update()

    def _layout_nodes(self) -> dict[str, QRectF]:
        if self.plan is None or not self.plan.tasks:
            return {}
        tasks = self.plan.task_map()
        levels: dict[str, int] = {}

        def depth(task_id: str, trail: set[str]) -> int:
            if task_id in levels:
                return levels[task_id]
            if task_id in trail:
                return 0
            task = tasks[task_id]
            value = 0 if not task.depends_on else 1 + max(depth(dep, trail | {task_id}) for dep in task.depends_on)
            levels[task_id] = value
            return value

        for task_id in tasks:
            depth(task_id, set())
        by_level: dict[int, list[str]] = {}
        for task_id, level in levels.items():
            by_level.setdefault(level, []).append(task_id)
        max_level = max(by_level) if by_level else 0
        width = max(1.0, self.width() - 40)
        height = max(1.0, self.height() - 34)
        node_width = min(204.0, max(142.0, width / max(1, max_level + 1) - 22))
        node_height = 58.0
        nodes: dict[str, QRectF] = {}
        for level, ids in by_level.items():
            x = 20 + level * (width / max(1, max_level + 1)) + (
                width / max(1, max_level + 1) - node_width
            ) / 2
            for index, task_id in enumerate(ids):
                y = 22 + (index + 0.5) * (height / max(1, len(ids))) - node_height / 2
                nodes[task_id] = QRectF(x, y, node_width, node_height)
        return nodes

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        if self.plan is None or not self.plan.tasks:
            painter.setPen(with_alpha(COLORS["muted"], 200))
            painter.setFont(font(11))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter,
                "Review a plan to reveal its dependency graph.",
            )
            painter.end()
            return
        self._nodes = self._layout_nodes()
        # Edges behind nodes
        for task in self.plan.tasks:
            for dependency in task.depends_on:
                if dependency not in self._nodes or task.task_id not in self._nodes:
                    continue
                source = self._nodes[dependency]
                target = self._nodes[task.task_id]
                start = QPointF(source.right(), source.center().y())
                end = QPointF(target.left(), target.center().y())
                color = QColor(TASK_TONES.get(self.statuses.get(dependency, "pending"), COLORS["faint"]))
                color.setAlpha(120)
                pen = QPen(color, 1.2)
                pen.setStyle(Qt.PenStyle.DashLine if self.statuses.get(dependency) != "completed" else Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                mid = (start.x() + end.x()) / 2
                painter.drawLine(start, QPointF(mid, start.y()))
                painter.drawLine(QPointF(mid, start.y()), QPointF(mid, end.y()))
                painter.drawLine(QPointF(mid, end.y()), end)
        for task in self.plan.tasks:
            box = self._nodes.get(task.task_id)
            if box is None:
                continue
            status = self.statuses.get(task.task_id, "pending")
            accent = QColor(TASK_TONES.get(status, COLORS["faint"]))
            bg = QColor(COLORS["surface_alt"])
            bg.setAlpha(245 if self.selected == task.task_id else 215)
            painter.setPen(QPen(accent if self.selected == task.task_id else with_alpha(accent.name(), 100),
                                2.0 if self.selected == task.task_id else 1.0))
            painter.setBrush(bg)
            painter.drawRoundedRect(box, 9, 9)
            painter.setPen(QPen(accent, 2))
            painter.drawLine(QPointF(box.left() + 1, box.top() + 9), QPointF(box.left() + 1, box.bottom() - 9))
            painter.setFont(font(9, QFont.Weight.DemiBold))
            painter.setPen(with_alpha(COLORS["ice"], 230))
            painter.drawText(box.adjusted(12, 8, -8, -26), Qt.AlignmentFlag.AlignLeft, task.task_id)
            painter.setFont(font(9))
            painter.setPen(with_alpha(COLORS["muted"], 230))
            painter.drawText(
                box.adjusted(12, 27, -8, -7),
                Qt.AlignmentFlag.AlignLeft,
                f"{task.role.replace('_', ' ')} · {status}",
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(QPointF(box.right() - 10, box.top() + 10), 2.4, 2.4)
        painter.end()

    def mousePressEvent(self, event: Any) -> None:
        point = event.position()
        for task_id, box in self._nodes.items():
            if box.contains(point):
                self.selected = task_id
                self.node_selected.emit(task_id)
                self.update()
                return
        super().mousePressEvent(event)


class AgentPresetDialog(QDialog):
    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context = context
        self.setWindowTitle("New agent identity")
        self.setMinimumWidth(470)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(11)
        outer.addWidget(label("WORKFORCE STUDIO", "Eyebrow"))
        outer.addWidget(label("Create an agent identity", "PageTitle"))
        form = QFormLayout()
        form.setSpacing(9)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Repository Cartographer")
        form.addRow("Name", self.name)
        self.model = QComboBox()
        for item in context.models:
            self.model.addItem(item.name)
        form.addRow("Model", self.model)
        self.role = QComboBox()
        for definition in ROLE_DEFINITIONS:
            self.role.addItem(definition.title, definition.key)
        form.addRow("Role", self.role)
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText(
            "Optional operating instructions. These are appended to the objective when the "
            "agent is used from Advanced Chat."
        )
        self.instructions.setMinimumHeight(100)
        form.addRow("Instructions", self.instructions)
        outer.addLayout(form)
        note = label(
            "The model, role and permissions remain host-enforced. An agent identity does not "
            "grant new tools or bypass approval.",
            "Faint", wrap=True,
        )
        outer.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _save(self) -> None:
        name = self.name.text().strip()
        model = self.model.currentText().strip()
        if not name or not model:
            QMessageBox.warning(self, "Agent identity incomplete", "Name and model are required.")
            return
        preset = {
            "name": name,
            "model": model,
            "role": str(self.role.currentData() or "general_assistant"),
            "instructions": self.instructions.toPlainText().strip()[:4000],
            "created_at": datetime.now(UTC).isoformat(),
        }
        presets = [
            item for item in self.context.settings.agent_presets
            if str(item.get("name") or "").casefold() != name.casefold()
        ]
        presets.append(preset)
        self.context.settings.agent_presets = presets
        save_settings(self.context.paths.config_file, self.context.settings)
        self.context.events.data_changed.emit("settings")
        self.accept()


class WorkforcePage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.plan: WorkforcePlan | None = None
        self.assignment_combos: dict[str, QComboBox] = {}
        self.statuses: dict[str, str] = {}
        self._worker: FunctionWorker | None = None
        self._cancel: Event | None = None
        self._activity_id = ""
        self._busy = False
        self._runs: list[dict[str, Any]] = []
        self._build()
        self._connect()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 9)
        root.setSpacing(14)
        self.review_button = button("Review plan", variant="Primary", on_click=self.review_plan)
        self.run_button = button("Approve and execute", on_click=self.execute_plan)
        self.run_button.setEnabled(False)
        self.cancel_button = button("Stop", variant="Danger", on_click=self.cancel_run)
        self.cancel_button.setEnabled(False)
        self.new_agent_button = button("New agent", variant="Ghost", on_click=self.new_agent)
        root.addWidget(PageHeader(
            "INTELLIGENCE / ORGANIZATION",
            "Workforce Studio",
            "Compose a bounded team, inspect the host-validated graph, then supervise execution.",
            actions=[self.new_agent_button, self.review_button, self.run_button, self.cancel_button],
        ))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.split = split
        self._narrow_split: bool | None = None
        split.setChildrenCollapsible(False)
        self.left_scroll = self._scroll_column(self._left_column(), minimum_width=380)
        self.right_scroll = self._scroll_column(self._right_column(), minimum_width=230)
        split.addWidget(self.left_scroll)
        split.addWidget(self.right_scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([860, 320])
        root.addWidget(split, 1)
        self.progress_label = label("No plan under execution.", "Faint")
        root.addWidget(self.progress_label)

    @staticmethod
    def _scroll_column(content: QWidget, *, minimum_width: int) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(content)
        scroll.setMinimumWidth(minimum_width)
        return scroll

    def _left_column(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 8, 20)
        layout.setSpacing(13)
        compose = Card(margins=(17, 15, 17, 13))
        compose.body.addWidget(SectionHeader(
            "Mission control",
            "The host validates every dependency, budget and artifact claim.",
        ))
        self.objective = QPlainTextEdit()
        self.objective.setPlaceholderText(
            "Describe the outcome, not the first action. The plan is generated locally and "
            "reviewed before any child executes."
        )
        self.objective.setMaximumHeight(92)
        compose.body.addWidget(self.objective)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.mode = QComboBox()
        self.mode.addItem("Analysis · analyse + verify", "analysis")
        self.mode.addItem("Delivery · analyse + deliver + verify", "delivery")
        controls.addWidget(self.mode)
        self.base_model = QComboBox()
        controls.addWidget(self.base_model, 1)
        compose.body.addLayout(controls)
        workspace_row = QHBoxLayout()
        workspace_row.setSpacing(7)
        workspace_row.addWidget(label("WORKSPACE", "MetricLabel"))
        self.workspace = label("", "Mono", wrap=False)
        self.workspace.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        workspace_row.addWidget(self.workspace, 1)
        compose.body.addLayout(workspace_row)
        layout.addWidget(compose)

        graph_card = Card(margins=(17, 14, 17, 12))
        graph_card.body.addWidget(SectionHeader(
            "Execution graph",
            "Dependencies, assignments and actual task transitions.",
        ))
        graph_card.body.addWidget(divider())
        self.graph = WorkforceGraph()
        self.graph.node_selected.connect(self._node_selected)
        graph_card.body.addWidget(self.graph, 1)
        layout.addWidget(graph_card, 1)

        assignments = Card(margins=(17, 14, 17, 12))
        assignments.body.addWidget(SectionHeader(
            "Task assignments",
            "Evidence fit is shown before approval. Assignments are host-checked.",
        ))
        self.assignment_scroll = QScrollArea()
        self.assignment_scroll.setWidgetResizable(True)
        self.assignment_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.assignment_content = QWidget()
        self.assignment_layout = QVBoxLayout(self.assignment_content)
        self.assignment_layout.setContentsMargins(0, 0, 4, 0)
        self.assignment_layout.setSpacing(7)
        self.assignment_scroll.setWidget(self.assignment_content)
        assignments.body.addWidget(self.assignment_scroll)
        assignments.setMinimumHeight(150)
        layout.addWidget(assignments)
        return widget

    def _right_column(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 20)
        layout.setSpacing(12)
        state = Card(margins=(14, 12, 14, 10))
        self.core = IntelligenceCore(compact=True, caption=False)
        self.core.setMinimumHeight(166)
        state.body.addWidget(self.core)
        self.core_caption = label("STANDBY", "Eyebrow")
        self.core_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state.body.addWidget(self.core_caption)
        self.state_detail = label("No orchestration active.", "Faint", wrap=True)
        self.state_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state.body.addWidget(self.state_detail)
        layout.addWidget(state)

        roster = Card(margins=(14, 13, 14, 12))
        roster.body.addWidget(SectionHeader("Agent roster", "Saved identities for Advanced Chat."))
        self.agent_list = QListWidget()
        self.agent_list.setMaximumHeight(138)
        prepare_list(self.agent_list, row_height=50)
        roster.body.addWidget(self.agent_list)
        roster_actions = QHBoxLayout()
        roster_actions.addWidget(button("Create", variant="Ghost", on_click=self.new_agent))
        roster_actions.addWidget(button("Delete", variant="Ghost", on_click=self.delete_agent))
        roster.body.addLayout(roster_actions)
        layout.addWidget(roster)

        journal = Card(margins=(14, 13, 14, 12))
        journal.body.addWidget(SectionHeader(
            "Durable runs",
            "Select a run to inspect its journal.",
        ))
        self.run_list = QListWidget()
        self.run_list.currentRowChanged.connect(self.show_run)
        prepare_list(self.run_list, row_height=50)
        journal.body.addWidget(self.run_list, 1)
        self.journal_detail = QScrollArea()
        self.journal_detail.setWidgetResizable(True)
        self.journal_detail.setFrameShape(QScrollArea.Shape.NoFrame)
        self.journal_detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.journal_detail.setMaximumHeight(210)
        self.journal_text = QLabel()
        self.journal_text.setObjectName("Muted")
        self.journal_text.setWordWrap(True)
        self.journal_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.journal_text.setTextFormat(Qt.TextFormat.RichText)
        self.journal_text.setContentsMargins(2, 4, 2, 4)
        self.journal_text.setMinimumWidth(0)
        self.journal_text.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.journal_detail.setWidget(self.journal_text)
        journal.body.addWidget(self.journal_detail)
        layout.addWidget(journal, 1)
        return widget

    def _connect(self) -> None:
        self.context.events.data_changed.connect(self._data_changed)
        self.context.events.core_state.connect(self._core_state)
        self.context.events.compose_workforce.connect(self.compose_objective)
        self.context.events.theme_changed.connect(self._apply_motion)
        self._apply_motion()
        self._apply_responsive_state()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        self._apply_responsive_state()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_responsive_state()

    def _apply_responsive_state(self) -> None:
        if not hasattr(self, "split"):
            return
        narrow = self.width() < 700
        if narrow == self._narrow_split:
            return
        self.split.setOrientation(
            Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        )
        self.split.setSizes([420, 260] if narrow else [860, 320])
        self._narrow_split = narrow

    def _apply_motion(self) -> None:
        self.core.set_motion(
            reduced=self.context.settings.reduced_motion,
            intensity=self.context.settings.animation_intensity,
        )

    def _core_state(self, state: str, detail: str) -> None:
        self.core.set_state(state, detail)
        self.core_caption.setText(state.replace("_", " ").upper())
        if detail:
            self.state_detail.setText(detail)

    def _data_changed(self, kind: str) -> None:
        if kind in {"models", "profiles", "workforce", "settings", "runs"}:
            self.refresh()

    def compose_objective(self, objective: str) -> None:
        self.objective.setPlainText(objective)
        self.objective.setFocus()
        self.context.set_state("planning", "Workforce objective received")

    def refresh(self) -> None:
        selected = self.base_model.currentText()
        self.base_model.blockSignals(True)
        self.base_model.clear()
        for item in self.context.models:
            self.base_model.addItem(item.name)
        if selected:
            self.base_model.setCurrentText(selected)
        self.base_model.blockSignals(False)
        workspace = self.context.workspace_path()
        self.workspace.setText(short_path(workspace))
        self.workspace.setToolTip(str(workspace))
        self._render_presets()
        self._refresh_runs()
        if self.plan is None:
            self._render_assignments()

    def _render_presets(self) -> None:
        self.agent_list.clear()
        for preset in self.context.settings.agent_presets:
            item = two_line_item(
                f"{preset.get('name')}\n{preset.get('role', 'specialist')} · {preset.get('model')}",
                data=preset.get("name"),
            )
            self.agent_list.addItem(item)
        if not self.context.settings.agent_presets:
            self.agent_list.addItem("No custom agent identities yet.")
            self.agent_list.setEnabled(False)
        else:
            self.agent_list.setEnabled(True)

    def new_agent(self) -> None:
        dialog = AgentPresetDialog(self.context, self)
        if dialog.exec():
            self._render_presets()

    def delete_agent(self) -> None:
        item = self.agent_list.currentItem()
        if item is None:
            return
        name = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not name:
            return
        confirmed = QMessageBox.question(
            self, "Delete agent identity",
            f"Remove the saved identity “{name}”? This does not delete the model or its profile.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.context.settings.agent_presets = [
            preset for preset in self.context.settings.agent_presets
            if str(preset.get("name") or "") != name
        ]
        save_settings(self.context.paths.config_file, self.context.settings)
        self.context.events.data_changed.emit("settings")
        self._render_presets()

    def review_plan(self) -> None:
        objective = self.objective.toPlainText().strip()
        if not objective:
            self.context.notify("Describe the workforce objective first.")
            return
        try:
            self.plan = (
                WorkforcePlanner.analysis_plan(objective)
                if str(self.mode.currentData()) == "analysis"
                else WorkforcePlanner.delivery_plan(objective)
            )
            self.plan.validate()
        except ValueError as exc:
            self.context.notify(f"Plan rejected: {exc}")
            return
        self.statuses = {task.task_id: "pending" for task in self.plan.tasks}
        self.graph.set_plan(self.plan, self.statuses)
        self._render_assignments()
        self.run_button.setEnabled(True)
        self.context.set_state("planning", f"{len(self.plan.tasks)} tasks validated")
        self.context.notify(
            f"Plan reviewed: {len(self.plan.tasks)} tasks, "
            f"{self.plan.max_total_steps} step budget. Review assignments before executing."
        )
        self.graph.setFocus()

    def _render_assignments(self) -> None:
        while self.assignment_layout.count():
            item = self.assignment_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.assignment_combos = {}
        if self.plan is None:
            self.assignment_layout.addWidget(label(
                "Review a plan to assign specialist models. No child agent can execute before "
                "the host validates the graph and you approve the assignments.",
                "Muted", wrap=True,
            ))
            self.assignment_layout.addStretch(1)
            return
        profiles = self.context.profiles
        for task in self.plan.tasks:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 3, 0, 3)
            layout.setSpacing(8)
            marker = QLabel("◆")
            marker.setStyleSheet(f"color:{COLORS['cyan_dim']};")
            layout.addWidget(marker)
            task_copy = QVBoxLayout()
            task_copy.setSpacing(0)
            task_copy.addWidget(label(f"{task.task_id} · {task.role}", "Body"))
            fit_labels = []
            for profile in profiles:
                matches = WorkforceMatcher.rank(task.role, [profile])
                if matches:
                    fit_labels.append(f"{profile.model_name}: {matches[0].fit_score:.0f}")
            task_copy.addWidget(label(
                " · ".join(fit_labels) or "No observed evidence for this role",
                "Faint", wrap=True,
            ))
            layout.addLayout(task_copy, 1)
            combo = QComboBox()
            for profile in profiles:
                combo.addItem(profile.model_name, profile.model_name)
            selected_model = self.base_model.currentText()
            index = combo.findData(selected_model)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(self._assignment_changed)
            self.assignment_combos[task.task_id] = combo
            layout.addWidget(combo)
            self.assignment_layout.addWidget(row)
        self.assignment_layout.addStretch(1)

    def _assignment_changed(self, *_args: object) -> None:
        del _args
        for task_id in self.assignment_combos:
            self.statuses[task_id] = "pending"
        self.graph.set_statuses(self.statuses)

    def execute_plan(self) -> None:
        if self._busy:
            return
        if self.plan is None:
            self.review_plan()
            return
        assignments: dict[str, AgentAssignment] = {}
        for task in self.plan.tasks:
            combo = self.assignment_combos.get(task.task_id)
            model = str(combo.currentData() or combo.currentText()) if combo else ""
            profile = self.context.profile_for(model)
            if profile is None:
                QMessageBox.warning(
                    self, "Profile missing",
                    f"No saved profile exists for {model or 'the selected model'}. "
                    "Profile it in Model Intelligence before delegation.",
                )
                return
            assignments[task.task_id] = AgentAssignment(
                task.task_id, model, profile, read_only=task.read_only
            )
        self.context.set_state("awaiting_approval", "Reviewing workforce authorization")
        confirmed = QMessageBox.question(
            self, "Approve workforce execution",
            f"{self.plan.summary()}\n\n"
            f"Workspace: {self.context.workspace_path()}\n\n"
            "The host will re-check models, budgets, dependency state, artifact claims and "
            "verifier verdicts. Child tools stay bounded by workspace policy.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            self.context.set_state("awaiting_approval", "Workforce approval withheld")
            self.context.notify("Workforce execution was rejected before it started.")
            return
        cancel = Event()
        self._cancel = cancel
        run_id = str(uuid.uuid4())
        owner = f"desktop-workforce:{uuid.uuid4().hex}"
        self._set_busy(True)
        self.cancel_button.setEnabled(True)
        self.context.set_state("delegating", f"Supervising {len(self.plan.tasks)} tasks")
        self._activity_id = self.context.activity.begin(
            "WORKFORCE", self.plan.objective, "Host-supervised task graph",
            cancel_event=cancel, cancel=lambda: cancel.set(), page="workforce",
            metadata={"run_id": run_id, "tasks": len(self.plan.tasks)},
        )
        worker = FunctionWorker(
            self._run_plan, self.plan, assignments, self.context.workspace_path(),
            run_id, owner, cancel,
            lambda event: worker.signals.progress.emit(
                f"{event.kind}\t{event.message}\t{event.data.get('task_id', '')}"
            ),
        )
        self._worker = worker
        worker.signals.progress.connect(self._on_event)
        worker.signals.result.connect(self._run_ready)
        worker.signals.error.connect(self._run_failed)
        worker.signals.finished.connect(self._run_finished)
        self.context.pool.start(worker)
        self._reset_statuses()
        self.graph.set_statuses(self.statuses)

    def _reset_statuses(self) -> None:
        for task_id in self.statuses:
            self.statuses[task_id] = "pending"

    def _run_plan(
        self,
        plan: WorkforcePlan,
        assignments: dict[str, AgentAssignment],
        workspace: Path,
        run_id: str,
        owner: str,
        cancel: Event,
        progress: Any,
    ) -> Any:
        if not self.context.repository.acquire_run_lease(
            run_id, owner, ttl_seconds=900,
            **current_process_identity().as_lease_metadata(),
        ):
            raise ValueError("Another workstation process already owns this workforce lease.")
        try:
            return WorkforceOrchestrator(
                self.context.client, self.context.repository,
                keep_alive=self.context.settings.keep_alive,
            ).run(
                plan, workspace, assignments, run_id=run_id, owner_id=owner,
                cancel_event=cancel, approval=lambda _message: True,
                installed_models=self.context.models, event=progress,
            )
        finally:
            self.context.repository.release_run_lease(run_id, owner)

    def _on_event(self, payload: str) -> None:
        kind, _, rest = payload.partition("\t")
        message, _, task_id = rest.partition("\t")
        if kind == "task_started" and task_id:
            self.statuses[task_id] = "running"
            self.graph.set_statuses(self.statuses)
        elif kind == "task_finished" and task_id:
            status = str(message).split()[-1] if message else "pending"
            if status.endswith("."):
                status = status[:-1]
            self.statuses[task_id] = status
            self.graph.set_statuses(self.statuses)
        elif kind == "task_blocked" and task_id:
            self.statuses[task_id] = "blocked"
            self.graph.set_statuses(self.statuses)
        self.progress_label.setText(message[:180])
        self.state_detail.setText(message[:180])
        if self._activity_id:
            self.context.activity.update(self._activity_id, message)
        task = self.plan.task_map().get(task_id) if self.plan and task_id else None
        if kind == "workforce_approved":
            self.context.set_state("workforce_delegation", "Approved · dispatching specialists")
        elif kind == "task_started":
            if task is not None and task.verdict_required:
                self.context.set_state("verifying", f"Independent verification · {task_id}")
            else:
                self.context.set_state("workforce_delegation", f"Delegating {task_id}")
        elif kind in {"child_tool_started", "child_tool_completed"}:
            self.context.set_state("tool_execution", message)
        elif kind == "child_thinking":
            self.context.set_state("generating", message)
        elif kind == "verification_recorded":
            self.context.set_state("verifying", message)
        elif kind in {"verification_failed", "verification_rejected"}:
            self.context.set_state("error", message)
        elif kind.startswith("child_"):
            self.context.set_state("workforce_delegation", message)
        elif kind == "workforce_completed":
            self.context.set_state(
                "complete" if "completed" in message.casefold() else "warning",
                message,
            )
        self._append_journal(kind, message)

    def _append_journal(self, kind: str, message: str) -> None:
        current = self.journal_text.text()
        safe = html.escape(message[:220])
        marker = f"<span style='color:{COLORS['cyan']};'>{html.escape(kind)}</span>"
        self.journal_text.setText(
            f"<div style='color:{COLORS['muted']};font-size:11px;'>{marker} · {safe}</div>"
            + (current if len(current) < 4000 else "")
        )

    def _run_ready(self, result: Any) -> None:
        status = str(getattr(result, "status", "unknown"))
        for task in getattr(result, "tasks", []) or []:
            task_id = str(getattr(task, "task_id", ""))
            if task_id:
                self.statuses[task_id] = str(getattr(task, "status", "pending"))
        self.graph.set_statuses(self.statuses)
        self.context.set_state(
            "complete" if status == "completed" else "warning",
            f"Workforce finished: {status}",
        )
        self.context.notify(f"Workforce finished: {status}")
        self.journal_text.setText(
            f"<div style='color:{COLORS['green' if status == 'completed' else 'amber']};'>"
            f"<b>Final status: {html.escape(status)}</b><br>"
            f"Verification: {html.escape(str(getattr(result, 'verification_passed', None)))}</div>"
            + self.journal_text.text()
        )
        self._refresh_runs()
        self._restore_standby_later()

    def _run_failed(self, message: str) -> None:
        cancelled = self._cancel is not None and self._cancel.is_set()
        self.context.set_state("standby" if cancelled else "error",
                               "Workforce cancelled" if cancelled else "Workforce failed")
        if not cancelled:
            QMessageBox.critical(self, "Workforce execution failed", message[:2400])
        self.progress_label.setText(
            "Workforce cancelled at a safe boundary." if cancelled else message.splitlines()[0][:200]
        )
        self._refresh_runs()

    def _run_finished(self) -> None:
        if self._activity_id:
            self.context.activity.finish(self._activity_id)
            self._activity_id = ""
        self._worker = None
        self._cancel = None
        self._set_busy(False)
        self.cancel_button.setEnabled(False)

    def _set_busy(self, value: bool) -> None:
        self._busy = value
        self.run_button.setEnabled(not value and self.plan is not None)
        self.review_button.setEnabled(not value)
        self.objective.setEnabled(not value)

    def cancel_run(self) -> None:
        if self._cancel is None:
            return
        self._cancel.set()
        self.cancel_button.setEnabled(False)
        self.context.set_state("awaiting_approval", "Workforce stop requested")
        self.progress_label.setText("Stop requested; waiting for a safe task boundary.")
        if self._activity_id:
            self.context.activity.cancel(self._activity_id)

    def _refresh_runs(self) -> None:
        try:
            self._runs = self.context.repository.list_workforce_runs(30)
        except Exception as exc:
            self.run_list.clear()
            self.run_list.addItem(f"Unable to load run journal: {exc}")
            return
        selected = self.run_list.currentRow()
        self.run_list.blockSignals(True)
        self.run_list.clear()
        for run in self._runs:
            item = two_line_item(
                f"{str(run.get('status') or 'unknown').upper()} · "
                f"{format_timestamp(run.get('created_at'), short=True)}\n"
                f"{render_compact(str(run.get('objective') or 'Untitled run'), 44)}",
                data=run.get("run_id"),
            )
            self.run_list.addItem(item)
        self.run_list.blockSignals(False)
        if selected >= 0 and selected < self.run_list.count():
            self.run_list.setCurrentRow(selected)
        elif self.run_list.count():
            self.run_list.setCurrentRow(0)

    def show_run(self, row: int) -> None:
        if row < 0 or row >= len(self._runs):
            return
        run = self._runs[row]
        run_id = str(run.get("run_id") or "")
        try:
            tasks = self.context.repository.list_workforce_tasks(run_id)
            events = self.context.repository.list_workforce_events(run_id)
            chain_ok = self.context.repository.verify_workforce_event_chain(run_id)
        except Exception as exc:
            self.journal_text.setText(f"Unable to read run: {html.escape(str(exc))}")
            return
        statuses = {str(item.get("task_id")): str(item.get("status") or "pending") for item in tasks}
        plan_data = run.get("plan") if isinstance(run.get("plan"), dict) else None
        if plan_data:
            try:
                plan = WorkforcePlan.from_dict(plan_data)
                self.graph.set_plan(plan, statuses)
            except (TypeError, ValueError):
                self.graph.set_plan(None, {})
        self.journal_text.setText(
            f"<div style='color:{COLORS['muted']};font-size:11px;'>"
            f"<b style='color:{COLORS['ice']};'>{html.escape(str(run.get('objective') or 'Workforce run'))}</b><br>"
            f"Status: {html.escape(str(run.get('status')))} · Journal chain: "
            f"{'valid' if chain_ok else 'INVALID'}<br>"
            f"Run ID: <span style='font-family:Consolas;'>{html.escape(run_id)}</span><br>"
            + "".join(
                f"<div style='margin-top:3px;'>#{int(event.get('sequence', 0)):03d} "
                f"{html.escape(str(event.get('kind') or 'event'))} · "
                f"{html.escape(str(event.get('message') or '')[:180])}</div>"
                for event in events[-8:]
            )
            + "</div>"
        )
        self.journal_text.setToolTip(str(run.get("objective") or ""))

    def _node_selected(self, task_id: str) -> None:
        task = self.plan.task_map().get(task_id) if self.plan else None
        if task is None:
            return
        self.progress_label.setText(
            f"{task.task_id} · {task.role} · {task.objective[:180]}"
        )

    def _restore_standby_later(self) -> None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(
            2600, lambda: self.context.set_state("standby", "Local intelligence ready")
        )


class DependencyLegend(QWidget):
    """Small legend retained for visual consistency in future graph variants."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for name, tone in (("pending", "muted"), ("running", "cyan"), ("complete", "green"),
                           ("failed", "red")):
            layout.addWidget(Pill(name, tone))
        layout.addStretch(1)

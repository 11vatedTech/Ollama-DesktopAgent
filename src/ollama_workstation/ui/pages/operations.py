"""Agent Operations: live work, cancellation, journals and technical inspection."""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.ui.components import (
    Card,
    PageHeader,
    button,
    divider,
    label,
)
from ollama_workstation.ui.formatting import pretty_json, render_compact
from ollama_workstation.ui.pages.common import (
    format_timestamp,
    prepare_list,
    two_line_item,
)
from ollama_workstation.ui.state import ActiveOperation, AppContext
from ollama_workstation.ui.theme import COLORS


class OperationsPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._runs: list[dict[str, Any]] = []
        self._active: list[ActiveOperation] = []
        self._current: dict[str, Any] | None = None
        self._build()
        self._connect()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(14)
        self.refresh_button = button("Refresh", on_click=self.refresh)
        self.technical = QCheckBox("Technical inspection")
        self.technical.toggled.connect(lambda _checked: self._render_current())
        technical_wrap = QWidget()
        technical_layout = QHBoxLayout(technical_wrap)
        technical_layout.setContentsMargins(0, 0, 0, 0)
        technical_layout.addWidget(self.technical)
        root.addWidget(PageHeader(
            "OPERATIONS / OBSERVABILITY",
            "Agent Operations",
            "Inspect every live task, tool boundary, durable journal and failure mode.",
            actions=[technical_wrap, self.refresh_button],
        ))
        stats = QHBoxLayout()
        stats.setSpacing(10)
        from ollama_workstation.ui.components import MetricTile

        self.active_tile = MetricTile("ACTIVE", "0", "operations in flight", tone="cyan")
        self.completed_tile = MetricTile("COMPLETED", "0", "durable agent runs", tone="green")
        self.failed_tile = MetricTile("ATTENTION", "0", "failed or interrupted", tone="amber")
        self.worker_tile = MetricTile("WORKFORCE", "0", "durable teams", tone="violet")
        for tile in (self.active_tile, self.completed_tile, self.failed_tile, self.worker_tile):
            stats.addWidget(tile)
        root.addLayout(stats)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._left_panel())
        split.addWidget(self._right_panel())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 900])
        root.addWidget(split, 1)

    def _left_panel(self) -> QWidget:
        panel = Card(margins=(14, 14, 14, 12))
        panel.setMinimumWidth(270)
        panel.setMaximumWidth(380)
        panel.body.addWidget(label("LIVE AND HISTORICAL", "Eyebrow"))
        panel.body.addWidget(label("Work register", "SectionTitle"))
        panel.body.addWidget(label("ACTIVE OPERATIONS", "MetricLabel"))
        self.active_list = QListWidget()
        self.active_list.setMaximumHeight(170)
        self.active_list.currentItemChanged.connect(self._selection_changed)
        prepare_list(self.active_list, row_height=50)
        panel.body.addWidget(self.active_list)
        panel.body.addWidget(divider())
        panel.body.addWidget(label("RUN JOURNAL", "MetricLabel"))
        self.run_list = QListWidget()
        self.run_list.currentItemChanged.connect(self._selection_changed)
        prepare_list(self.run_list, row_height=50)
        panel.body.addWidget(self.run_list, 1)
        panel.body.addWidget(button(
            "Open recovery", variant="Ghost", on_click=lambda: self.context.navigate("history")
        ))
        return panel

    def _right_panel(self) -> QWidget:
        panel = Card(margins=(18, 16, 18, 14))
        header = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(2)
        self.detail_title = label("Select an operation", "SectionTitle")
        self.detail_meta = label(
            "Live operations and durable journals appear here.", "SectionMeta", wrap=True
        )
        copy.addWidget(self.detail_title)
        copy.addWidget(self.detail_meta)
        header.addLayout(copy, 1)
        self.cancel_button = button("Stop selected", variant="Danger", on_click=self.cancel_current)
        self.cancel_button.setEnabled(False)
        header.addWidget(self.cancel_button)
        panel.body.addLayout(header)
        panel.body.addWidget(divider())
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        panel.body.addWidget(self.detail, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        panel.body.addWidget(self.progress)
        return panel

    def _connect(self) -> None:
        self.context.activity.changed.connect(self.refresh)
        self.context.events.data_changed.connect(self._data_changed)

    def _data_changed(self, kind: str) -> None:
        if kind in {"runs", "workforce", "settings"}:
            self.refresh()

    def refresh(self) -> None:
        self._active = self.context.activity.active()
        self._refresh_active_list()
        try:
            self._runs = self.context.repository.list_runs(80)
            workforce = self.context.repository.list_workforce_runs(80)
        except Exception:
            self._runs = []
            workforce = []
        self._refresh_run_list()
        completed = sum(1 for run in self._runs if run.get("status") == "completed")
        failed = sum(
            1 for run in self._runs
            if run.get("status") not in {"completed", "running"}
        )
        self.active_tile.set_value(str(len(self._active)), tone="cyan")
        self.completed_tile.set_value(str(completed))
        self.failed_tile.set_value(str(failed), tone="amber" if failed else "green")
        self.worker_tile.set_value(str(len(workforce)))
        if self._active:
            self.active_list.setCurrentRow(0)
        elif self.run_list.count():
            self.run_list.setCurrentRow(0)
        else:
            self._render_empty()

    def _refresh_active_list(self) -> None:
        self.active_list.blockSignals(True)
        self.active_list.clear()
        for operation in self._active:
            item = two_line_item(
                f"{operation.kind.upper()} · {operation.title}\n{operation.detail or 'in progress'}",
                data=("active", operation.operation_id),
            )
            self.active_list.addItem(item)
        if not self._active:
            placeholder = QListWidgetItem("Intelligence core is standing by.")
            placeholder.setForeground(Qt.GlobalColor.gray)
            self.active_list.addItem(placeholder)
        self.active_list.blockSignals(False)

    def _refresh_run_list(self) -> None:
        selected = self._current
        self.run_list.blockSignals(True)
        self.run_list.clear()
        for run in self._runs:
            item = two_line_item(
                f"{str(run.get('status') or 'unknown').upper()} · "
                f"{format_timestamp(run.get('created_at'), short=True)}\n"
                f"{run.get('agent_title') or 'Agent'} · {render_compact(str(run.get('task') or ''), 44)}",
                data=("run", run.get("run_id")),
            )
            item.setForeground(
                Qt.GlobalColor.darkYellow if run.get("status") not in {"completed"} else
                Qt.GlobalColor.white
            )
            self.run_list.addItem(item)
        self.run_list.blockSignals(False)
        if selected and selected.get("kind") == "run":
            for index in range(self.run_list.count()):
                data = self.run_list.item(index).data(Qt.ItemDataRole.UserRole)
                if isinstance(data, tuple) and data[1] == selected.get("id"):
                    self.run_list.setCurrentRow(index)
                    break

    def _selection_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is None:
            return
        data = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        kind, identifier = data
        if kind == "active":
            operation = self.context.activity.get(str(identifier))
            if operation is None:
                return
            self._current = {"kind": "active", "id": identifier, "operation": operation}
            self._render_active(operation)
        else:
            run = next(
                (item for item in self._runs if item.get("run_id") == identifier), None
            )
            if run is None:
                return
            self._current = {"kind": "run", "id": identifier, "run": run}
            self._render_run(run)

    def _render_active(self, operation: ActiveOperation) -> None:
        self.detail_title.setText(operation.title)
        self.detail_meta.setText(
            f"{operation.kind} · started {format_timestamp(operation.started_at)} · "
            f"{operation.page or 'workspace'}"
        )
        self.cancel_button.setEnabled(operation.cancel_event is not None or operation.cancel is not None)
        self.progress.show()
        rows = "".join(
            f"<tr><td style='color:{COLORS['muted']};'>{html.escape(str(key))}</td>"
            f"<td style='font-family:Consolas;color:{COLORS['text_secondary']};'>"
            f"{html.escape(str(value))}</td></tr>"
            for key, value in operation.metadata.items()
        )
        self.detail.setHtml(
            f"<div style='color:{COLORS['text_secondary']};font-family:Segoe UI;font-size:12px;'>"
            f"<p><b style='color:{COLORS['ice']};'>Operation in flight</b></p>"
            f"<p>{html.escape(operation.detail)}</p>"
            f"<table cellspacing='6'>{rows}</table>"
            f"<p style='color:{COLORS['faint']};'>Stop requests are cooperative. A provider or "
            "tool operation may finish before the next safe boundary.</p></div>"
        )

    def _render_run(self, run: dict[str, Any]) -> None:
        self.progress.hide()
        self.cancel_button.setEnabled(False)
        run_id = str(run.get("run_id") or "")
        self.detail_title.setText(str(run.get("agent_title") or "Agent run"))
        self.detail_meta.setText(
            f"{str(run.get('status') or 'unknown').upper()} · "
            f"{format_timestamp(run.get('created_at'))} · {run_id}"
        )
        try:
            events = self.context.repository.list_run_events(run_id)
        except Exception:
            events = []
        if not events:
            events = [
                {
                    "sequence": index + 1, "kind": item.get("kind", "event"),
                    "message": item.get("message", ""), "timestamp": item.get("timestamp", ""),
                    "data": item.get("data", {}),
                }
                for index, item in enumerate(run.get("trace") or [])
                if isinstance(item, dict)
            ]
        if self._current is None:
            return
        self._current["events"] = events
        self._current["run"] = run
        self._render_current()

    def _render_current(self) -> None:
        if not self._current:
            return
        if self._current.get("kind") == "active":
            return
        run = self._current.get("run") or {}
        events = self._current.get("events") or []
        if self.technical.isChecked():
            payload = {"run": run, "events": events}
            self.detail.setHtml(
                f"<pre style='color:{COLORS['text_secondary']};font-family:Consolas;"
                f"font-size:11px;white-space:pre-wrap;'>{html.escape(pretty_json(payload)[:30000])}</pre>"
            )
            return
        rows = []
        for event in events:
            message = str(event.get("message") or "")
            rows.append(
                f"<div style='border-left:2px solid {COLORS['border_bright']};"
                f"padding:3px 0 3px 9px;margin:5px 0;'>"
                f"<span style='color:{COLORS['cyan']};font-size:10px;font-weight:600;'>"
                f"#{int(event.get('sequence', 0)):03d} · {html.escape(str(event.get('kind') or 'event'))}</span>"
                f"<div style='color:{COLORS['text_secondary']};font-size:12px;'>"
                f"{html.escape(message[:500])}</div></div>"
            )
        task = html.escape(str(run.get("task") or ""))
        status = str(run.get("status") or "unknown")
        self.detail.setHtml(
            f"<div style='color:{COLORS['text_secondary']};font-family:Segoe UI;font-size:12px;'>"
            f"<p><b style='color:{COLORS['ice']};'>Objective</b><br>{task}</p>"
            f"<p><b style='color:{COLORS['ice']};'>Execution journal</b> "
            f"<span style='color:{COLORS['faint']};'>({len(events)} events · {status})</span></p>"
            f"{''.join(rows) or '<p>No journal events were recorded.</p>'}</div>"
        )

    def cancel_current(self) -> None:
        if not self._current or self._current.get("kind") != "active":
            return
        operation = self._current.get("operation")
        if not isinstance(operation, ActiveOperation):
            return
        if operation.cancel_event is None and operation.cancel is None:
            return
        self.context.activity.cancel(operation.operation_id)
        self.cancel_button.setEnabled(False)
        self.context.notify("Stop requested. Waiting for a safe execution boundary.")

    def _render_empty(self) -> None:
        self._current = None
        self.detail_title.setText("No operations selected")
        self.detail_meta.setText("Start a conversation or workforce run to produce a live trace.")
        self.detail.setHtml(
            f"<div style='color:{COLORS['muted']};padding:24px;font-size:12px;'>"
            "The execution journal is intentionally local. Select a run or active operation "
            "from the register to inspect its boundaries.</div>"
        )
        self.cancel_button.setEnabled(False)
        self.progress.hide()


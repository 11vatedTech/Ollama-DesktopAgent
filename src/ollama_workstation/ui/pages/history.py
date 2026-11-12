"""Run History and verified recovery controls."""

from __future__ import annotations

import html
import uuid
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.binding import validate_installed_model_binding
from ollama_workstation.agents.mcp_gateway import CompositeToolRegistry, MCPToolGateway
from ollama_workstation.agents.recovery import (
    RecoveryCandidate,
    RecoveryStatus,
    load_recovery_candidates,
)
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.ui.components import (
    Card,
    PageHeader,
    button,
    divider,
    label,
)
from ollama_workstation.ui.pages.common import (
    format_timestamp,
    prepare_list,
    two_line_item,
)
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.theme import COLORS
from ollama_workstation.ui.workers import FunctionWorker


class HistoryPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._runs: list[dict[str, Any]] = []
        self._candidates: list[RecoveryCandidate] = []
        self._worker: FunctionWorker | None = None
        self._cancel: Event | None = None
        self._activity_id = ""
        self._build()
        self._connect()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(14)
        self.refresh_button = button("Refresh", on_click=self.refresh)
        root.addWidget(PageHeader(
            "OPERATIONS / MEMORY",
            "Run History & Recovery",
            "Search durable execution journals and resume only checkpoints the host can verify.",
            actions=[self.refresh_button],
        ))
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._left_panel())
        split.addWidget(self._right_panel())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900])
        root.addWidget(split, 1)

    def _left_panel(self) -> QWidget:
        panel = Card(margins=(14, 14, 14, 12))
        panel.setMinimumWidth(280)
        panel.setMaximumWidth(390)
        panel.body.addWidget(label("DURABLE EXECUTION", "Eyebrow"))
        panel.body.addWidget(label("Run archive", "SectionTitle"))
        self.search = QLineEdit()
        self.search.setObjectName("Search")
        self.search.setPlaceholderText("Search objective, model or status…")
        self.search.textChanged.connect(self._filter_runs)
        panel.body.addWidget(self.search)
        self.run_list = QListWidget()
        self.run_list.currentRowChanged.connect(self._show_run)
        prepare_list(self.run_list, row_height=50)
        panel.body.addWidget(self.run_list, 1)
        panel.body.addWidget(divider())
        panel.body.addWidget(label("RECOVERY CANDIDATES", "MetricLabel"))
        self.recovery_list = QListWidget()
        self.recovery_list.setMaximumHeight(154)
        self.recovery_list.currentRowChanged.connect(self._show_candidate)
        prepare_list(self.recovery_list, row_height=50)
        panel.body.addWidget(self.recovery_list)
        row = QHBoxLayout()
        row.setSpacing(5)
        self.resume_button = button("Resume", variant="Secondary", on_click=self.resume_run)
        self.discard_button = button("Discard", variant="Ghost", on_click=self.discard_run)
        self.resume_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        row.addWidget(self.resume_button)
        row.addWidget(self.discard_button)
        row.addWidget(button("Refresh", variant="Ghost", on_click=self.refresh))
        panel.body.addLayout(row)
        self.recovery_note = label(
            "Resume is blocked unless the model digest, workspace and tool manifest still match.",
            "Faint", wrap=True,
        )
        panel.body.addWidget(self.recovery_note)
        return panel

    def _right_panel(self) -> QWidget:
        panel = Card(margins=(18, 16, 18, 15))
        header = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(2)
        self.detail_title = label("Select a durable run", "SectionTitle")
        self.detail_meta = label(
            "Ordered events, failure diagnostics and verified recovery state.", "SectionMeta", wrap=True
        )
        copy.addWidget(self.detail_title)
        copy.addWidget(self.detail_meta)
        header.addLayout(copy, 1)
        header.addWidget(button(
            "Agent operations", variant="Ghost",
            on_click=lambda: self.context.navigate("operations"),
        ))
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
        self.context.events.data_changed.connect(self._data_changed)

    def _data_changed(self, kind: str) -> None:
        if kind in {"runs", "workforce", "settings"}:
            self.refresh()

    def refresh(self) -> None:
        if self._worker is not None:
            return
        try:
            runs = self.context.repository.list_runs(200)
        except Exception as exc:
            self.detail.setHtml(f"<p style='color:{COLORS['red']};'>{html.escape(str(exc))}</p>")
            runs = []
        self._runs = runs
        self._filter_runs()
        try:
            self._candidates = list(load_recovery_candidates(self.context.repository))
        except Exception as exc:
            self._candidates = []
            self.recovery_note.setText(f"Recovery scan failed: {type(exc).__name__}")
        self.recovery_list.clear()
        labels = {
            RecoveryStatus.RESUMABLE: "RESUMABLE",
            RecoveryStatus.LEASED: "LEASED / ACTIVE",
            RecoveryStatus.NEEDS_REVIEW: "REVIEW REQUIRED",
            RecoveryStatus.STALE: "STALE",
            RecoveryStatus.CORRUPT: "CORRUPT",
            RecoveryStatus.SUPERSEDED: "SUPERSEDED",
            RecoveryStatus.EXHAUSTED: "BUDGET EXHAUSTED",
        }
        for candidate in self._candidates:
            item = two_line_item(
                f"{labels[candidate.status]} · {candidate.agent_title or 'Agent'} · "
                f"step {candidate.step}/{candidate.step_budget or '?'}\n"
                f"{candidate.reason}"
            )
            if candidate.resumable:
                item.setForeground(Qt.GlobalColor.cyan)
            else:
                item.setForeground(Qt.GlobalColor.yellow)
            self.recovery_list.addItem(item)
        if not self._candidates:
            self.recovery_list.addItem("No durable checkpoints require attention.")
            self.resume_button.setEnabled(False)
            self.discard_button.setEnabled(False)
        else:
            self.recovery_list.setCurrentRow(0)
        if not self._runs:
            self.detail_title.setText("No execution history")
            self.detail_meta.setText("Completed and interrupted runs will appear here.")

    def _filter_runs(self, *_args: object) -> None:
        query = self.search.text().strip().casefold()
        self.run_list.blockSignals(True)
        self.run_list.clear()
        for run in self._runs:
            haystack = " ".join(str(run.get(key) or "") for key in (
                "task", "agent_title", "model_name", "status", "run_id"
            )).casefold()
            if query and query not in haystack:
                continue
            item = two_line_item(
                f"{str(run.get('status') or 'unknown').upper()} · "
                f"{format_timestamp(run.get('created_at'), short=True)}\n"
                f"{run.get('agent_title') or 'Agent'} · "
                f"{_compact(str(run.get('task') or ''), 48)}",
                data=run.get("run_id"),
            )
            self.run_list.addItem(item)
        self.run_list.blockSignals(False)
        if self.run_list.count():
            self.run_list.setCurrentRow(0)

    def _show_run(self, row: int) -> None:
        if row < 0 or row >= self.run_list.count():
            return
        item = self.run_list.item(row)
        run_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        run = next((entry for entry in self._runs if entry.get("run_id") == run_id), None)
        if run is None:
            return
        try:
            events = self.context.repository.list_run_events(run_id)
        except Exception:
            events = []
        self.detail_title.setText(str(run.get("agent_title") or "Agent run"))
        self.detail_meta.setText(
            f"{str(run.get('status') or 'unknown').upper()} · {format_timestamp(run.get('created_at'))} "
            f"· {run_id}"
        )
        rows = []
        for event in events[-160:]:
            message, _ = WorkspaceTools.redact_sensitive_text(str(event.get("message") or ""))
            rows.append(
                f"<div style='border-left:2px solid {COLORS['border_bright']};padding:3px 0 3px 9px;"
                f"margin:5px 0;'><span style='color:{COLORS['cyan']};font-size:10px;'>"
                f"#{int(event.get('sequence', 0)):03d} · {html.escape(str(event.get('kind') or 'event'))}"
                f"</span><div style='color:{COLORS['text_secondary']};font-size:12px;'>"
                f"{html.escape(message[:700])}</div></div>"
            )
        task, _ = WorkspaceTools.redact_sensitive_text(str(run.get("task") or ""))
        self.detail.setHtml(
            f"<div style='color:{COLORS['text_secondary']};font-size:12px;font-family:Segoe UI;'>"
            f"<p><b style='color:{COLORS['ice']};'>Objective</b><br>{html.escape(task)}</p>"
            f"<p><b style='color:{COLORS['ice']};'>Model</b> {html.escape(str(run.get('model_name') or '—'))} "
            f"· <b>Status</b> {html.escape(str(run.get('status') or '—'))}</p>"
            f"<h3 style='color:{COLORS['ice']};font-size:13px;'>Event journal</h3>"
            f"{''.join(rows) or '<p>No events recorded.</p>'}</div>"
        )

    def _show_candidate(self, row: int) -> None:
        if row < 0 or row >= len(self._candidates):
            return
        candidate = self._candidates[row]
        self.recovery_note.setText(
            f"{candidate.status.value.upper()} · {candidate.reason}"
        )
        self.resume_button.setEnabled(bool(candidate.resumable))
        self.discard_button.setEnabled(True)

    def resume_run(self) -> None:
        if self._worker is not None:
            self.context.notify("A recovery operation is already running.")
            return
        row = self.recovery_list.currentRow()
        if row < 0 or row >= len(self._candidates):
            return
        if self._candidates[row].status is not RecoveryStatus.RESUMABLE:
            self.context.notify("This checkpoint is not resumable.")
            return
        candidate = self._candidates[row]
        if not candidate.resumable:
            QMessageBox.information(
                self, "Run not resumable",
                f"{candidate.reason}\n\nReview the journal, then discard the checkpoint.",
            )
            return
        try:
            checkpoint = self.context.repository.get_run_checkpoint(candidate.run_id)
        except ValueError as exc:
            self.context.notify(f"Checkpoint integrity verification failed: {exc}")
            return
        if checkpoint is None:
            self.refresh()
            return
        profile = self.context.repository.get_profile(
            str(checkpoint.get("model_name") or ""),
            str(checkpoint.get("model_digest") or ""),
        )
        if profile is None:
            QMessageBox.warning(
                self, "Profile missing",
                "The profile bound to this checkpoint is unavailable. Resume is refused.",
            )
            return
        workspace = Path(str(checkpoint.get("workspace") or ""))
        if not workspace.is_dir():
            QMessageBox.warning(self, "Workspace missing", f"The bound workspace is missing:\n{workspace}")
            return
        task = str((checkpoint.get("payload") or {}).get("task") or "")
        confirmed = QMessageBox.question(
            self, "Resume verified run",
            f"Resume {candidate.agent_title or 'agent'} at step {candidate.step + 1} of "
            f"{candidate.step_budget}?\n\nModel: {checkpoint.get('model_name')}\n"
            f"Workspace: {workspace}\n\nTask:\n{task[:600]}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        owner = f"desktop-recovery:{uuid.uuid4().hex}"
        if not self.context.repository.acquire_run_lease(candidate.run_id, owner, ttl_seconds=900):
            QMessageBox.warning(self, "Run is leased", "Another process owns this run lease.")
            return
        cancel = Event()
        self._cancel = cancel
        self._progress_show()
        self.context.set_state("thinking", f"Resuming {candidate.run_id[:8]}")
        self._activity_id = self.context.activity.begin(
            "RECOVERY", candidate.agent_title or "Agent recovery",
            f"Resuming at verified step {candidate.step + 1}", cancel_event=cancel,
            cancel=lambda: cancel.set(), page="history",
            metadata={"run_id": candidate.run_id},
        )
        worker = FunctionWorker(
            self._resume_task, profile, task, workspace, checkpoint, candidate.run_id,
            owner, cancel,
        )
        self._worker = worker
        worker.signals.progress.connect(self.context.notify)
        worker.signals.result.connect(self._resume_ready)
        worker.signals.error.connect(self._resume_failed)
        worker.signals.finished.connect(self._resume_finished)
        self.context.pool.start(worker)

    def _resume_task(
        self,
        profile: Any,
        task: str,
        workspace: Path,
        checkpoint: dict[str, Any],
        run_id: str,
        owner: str,
        cancel: Event,
    ) -> Any:
        try:
            installed = next(
                (item for item in self.context.profiler.inventory()
                 if item.name == profile.model_name), None
            )
            if installed is None:
                raise ValueError("The bound model is not installed.")
            validate_installed_model_binding(profile, installed)
            tools = WorkspaceTools(workspace, allow_shell=False, cancel_event=cancel)
            with MCPToolGateway(self.context.settings.mcp_servers, cancel_event=cancel) as gateway:
                registry = CompositeToolRegistry(tools, gateway)
                runtime = AgentRuntime(
                    self.context.client, max_steps=self.context.settings.max_agent_steps,
                    keep_alive=self.context.settings.keep_alive,
                    repository=self.context.repository, memory_store=self.context.memory_store,
                )
                return runtime.run(
                    profile.model_name, profile, task, registry,
                    cancel_event=cancel, resume_checkpoint=checkpoint,
                    lease_owner=owner,
                )
        finally:
            self.context.repository.release_run_lease(run_id, owner)

    def _resume_ready(self, result: Any) -> None:
        self.detail_title.setText("Recovery completed")
        self.detail.setText(
            f"<div style='color:{COLORS['text_secondary']};font-size:12px;'>"
            f"<p><b style='color:{COLORS['ice']};'>Status</b> {html.escape(str(getattr(result, 'status', 'unknown')))}</p>"
            f"<p>{html.escape(str(getattr(result, 'final_message', '') or 'No final message.'))}</p></div>"
        )
        self.context.set_state("complete", "Recovery completed")
        self.refresh()

    def _resume_failed(self, message: str) -> None:
        cancelled = self._cancel is not None and self._cancel.is_set()
        self.context.set_state("standby" if cancelled else "warning",
                               "Recovery stopped" if cancelled else "Recovery failed")
        if not cancelled:
            QMessageBox.critical(self, "Recovery failed", message[:2400])

    def _resume_finished(self) -> None:
        if self._activity_id:
            self.context.activity.finish(self._activity_id)
            self._activity_id = ""
        self._worker = None
        self._cancel = None
        self.progress.hide()

    def _progress_show(self) -> None:
        self.progress.show()

    def discard_run(self) -> None:
        row = self.recovery_list.currentRow()
        if row < 0 or row >= len(self._candidates):
            return
        self.discard_button.setEnabled(False)
        candidate = self._candidates[row]
        confirmed = QMessageBox.question(
            self, "Discard checkpoint",
            f"Delete the recovery checkpoint for run {candidate.run_id[:8]}?\n\n"
            "The event journal and run history remain. Only the resume snapshot is removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        deleted = self.context.repository.delete_run_checkpoint(candidate.run_id)
        self.context.notify(
            f"Discarded checkpoint {candidate.run_id[:8]}." if deleted
            else "Checkpoint was already absent."
        )
        self.refresh()


def _compact(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"

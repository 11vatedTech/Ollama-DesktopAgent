"""Agent Foundry: qualify models, manage versioned agents, run them verified, curate memory.

The page is a thin view over :class:`FoundryController`; every action uses the
same services, gates and evidence as the CLI. Long operations run on the shared
thread pool and are registered with the activity registry so they can be
cancelled from anywhere in the shell.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agentification.foundry import FoundryController
from ollama_workstation.models.identity import ROLE_DEFINITIONS
from ollama_workstation.ui.components import Card, PageHeader, Pill, button, label
from ollama_workstation.ui.pages.common import format_bytes, format_timestamp
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.workers import FunctionWorker

STATUS_TONES = {"qualified": "green", "provisional": "amber", "unqualified": "red",
                "not qualified": "muted", "active": "green", "draft": "muted",
                "disabled": "amber", "retired": "red"}


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _cell(text: Any, data: Any = None) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    return item


class AgentFoundryPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._controller: FoundryController | None = None
        self._cancel: Event | None = None
        self._activity_id = ""
        self._busy = False
        # Strong references to in-flight workers: without them the Python wrapper
        # (and its signals object) can be collected mid-run and results are lost.
        self._workers: set[FunctionWorker] = set()
        self._build()

    # -- wiring ---------------------------------------------------------------------------
    @property
    def controller(self) -> FoundryController:
        if self._controller is None or self._controller.client is not self.context.client:
            self._controller = FoundryController(
                self.context.client, self.context.repository, self.context.settings,
                memory_store=self.context.memory_store)
        return self._controller

    def on_show(self) -> None:
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 16)
        root.setSpacing(12)
        self.refresh_button = button("Refresh", on_click=self.refresh)
        self.cancel_button = button("Cancel", variant="Secondary", on_click=self._cancel_work)
        self.cancel_button.setEnabled(False)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.refresh_button)
        row.addWidget(self.cancel_button)
        root.addWidget(PageHeader(
            "INTELLIGENCE / AGENTS", "Agent Foundry",
            "Qualify each model on verified tasks, bind agents to exact model digests, "
            "and run them under measured budgets with independent verification.",
            actions=[actions]))
        self.status = label("", "Muted", wrap=True)
        root.addWidget(self.status)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._qualification_tab(), "Qualification")
        self.tabs.addTab(self._agents_tab(), "Agents")
        self.tabs.addTab(self._run_tab(), "Run")
        self.tabs.addTab(self._memory_tab(), "Memory")
        root.addWidget(self.tabs, 1)

    # -- qualification tab ------------------------------------------------------------------
    def _qualification_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        self.models_table = _table(["Model", "Size", "Status", "Harness", "Verified",
                                    "Qualified", "Weak workloads"])
        self.models_table.itemSelectionChanged.connect(self._show_model_detail)
        layout.addWidget(self.models_table, 3)
        controls = QHBoxLayout()
        controls.addWidget(label("Trials per task", "Muted"))
        self.trials = QSpinBox()
        self.trials.setRange(1, 10)
        self.trials.setValue(2)
        controls.addWidget(self.trials)
        self.qualify_shell = QCheckBox("Include run_command (executes model-written code)")
        controls.addWidget(self.qualify_shell)
        controls.addStretch(1)
        self.admission_button = button("Check resources", variant="Secondary",
                                       on_click=self._check_admission)
        controls.addWidget(self.admission_button)
        self.qualify_button = button("Qualify selected", on_click=self._qualify)
        controls.addWidget(self.qualify_button)
        layout.addLayout(controls)
        self.model_detail = QTextBrowser()
        self.model_detail.setOpenExternalLinks(False)
        layout.addWidget(self.model_detail, 2)
        return tab

    def _selected_model(self) -> str:
        current = self.models_table.currentRow()
        rows = [current] if current >= 0 else []
        if not rows:
            return ""
        item = self.models_table.item(rows[0], 0)
        return "" if item is None else str(item.text())

    def _show_model_detail(self) -> None:
        model = self._selected_model()
        if not model:
            return
        row = next((item for item in self._model_rows if item.name == model), None)
        if row is None:
            return
        record = self.controller.latest_qualification(row.name, row.digest)
        if record is None:
            self.model_detail.setHtml(
                f"<p><b>{html.escape(model)}</b> has no qualification for digest "
                f"<code>{html.escape(row.digest[:16])}</code>.</p><p>No path in the workstation "
                "will run it as an agent until it is qualified (or an operator override is set "
                "in Settings).</p>")
            return
        parts = [f"<p><b>{html.escape(model)}</b> &mdash; {html.escape(record.status.upper())}"
                 f" on <b>{html.escape(str(record.selected_harness))}</b></p><table>"]
        for mode, result in record.harnesses.items():
            parts.append(
                f"<tr><td>{html.escape(mode)}</td><td>{html.escape(result.status)}</td>"
                f"<td>{result.passed}/{result.attempts} verified</td>"
                f"<td>95% CI {result.interval_low:.2f}&ndash;{result.interval_high:.2f}</td></tr>")
        parts.append("</table><p>Workloads: " + ", ".join(
            f"{html.escape(key)}={html.escape(value)}"
            for key, value in record.workload_suitability.items()) + "</p>")
        failures = [attempt for attempt in record.attempts if not attempt.passed]
        if failures:
            parts.append("<p><b>Failed attempts</b></p><ul>")
            for attempt in failures[:12]:
                parts.append(f"<li>{html.escape(attempt.harness)} / "
                             f"{html.escape(attempt.task_id)}: "
                             f"{html.escape(attempt.detail[:300])}</li>")
            parts.append("</ul>")
        parts.append(f"<p>Bound to Ollama {html.escape(record.runtime_version)}, prompt "
                     f"{html.escape(record.prompt_version)}, suite "
                     f"{html.escape(record.suite_id)}@{html.escape(record.suite_version)}.</p>")
        self.model_detail.setHtml("".join(parts))

    def _qualify(self) -> None:
        model = self._selected_model()
        if not model:
            self.status.setText("Select a model to qualify.")
            return
        cancel = Event()
        self._start(
            f"Qualifying {model}", self.controller.qualify, model,
            trials=self.trials.value(), shell=self.qualify_shell.isChecked(),
            cancel_event=cancel, cancel=cancel, with_progress=True,
            on_result=lambda record: self._after(
                f"{record.model_name}: {record.status} "
                f"(harness {record.selected_harness or 'none'})"))

    def _check_admission(self) -> None:
        model = self._selected_model()
        if not model:
            self.status.setText("Select a model to check.")
            return
        self._start(f"Checking resources for {model}", self.controller.admission_for, model,
                    on_result=lambda decision: self._after(
                        f"{decision['model']}: {decision['outcome']} - "
                        + "; ".join(decision["reasons"]), refresh=False))

    # -- agents tab -------------------------------------------------------------------------
    def _agents_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        left = QVBoxLayout()
        self.agents_table = _table(["Agent", "Status", "Version", "Model", "Role", "Readiness"])
        self.agents_table.itemSelectionChanged.connect(self._show_agent_detail)
        left.addWidget(self.agents_table, 3)
        buttons = QHBoxLayout()
        self.activate_button = button("Activate", on_click=self._activate)
        self.disable_button = button("Disable", variant="Secondary", on_click=self._disable)
        buttons.addWidget(self.activate_button)
        buttons.addWidget(self.disable_button)
        buttons.addStretch(1)
        left.addLayout(buttons)
        self.agent_detail = QTextBrowser()
        left.addWidget(self.agent_detail, 2)
        layout.addLayout(left, 3)

        form_card = Card(margins=(16, 14, 16, 14))
        form_card.body.addWidget(label("New agent", "SectionTitle"))
        form = QFormLayout()
        self.agent_name = QLineEdit()
        self.agent_name.setPlaceholderText("e.g. Repo Scribe")
        self.agent_model = QComboBox()
        self.agent_role = QComboBox()
        self.agent_role.addItem("Earned role (from profile)", None)
        for definition in ROLE_DEFINITIONS:
            self.agent_role.addItem(definition.title, definition.key)
        self.agent_write = QCheckBox("May write files")
        self.agent_write.setChecked(True)
        self.agent_shell = QCheckBox("May run Python (contained)")
        self.agent_git = QCheckBox("May inspect git (read-only)")
        self.agent_memory = QCheckBox("Use memory")
        self.agent_memory.setChecked(True)
        self.agent_skills = QListWidget()
        self.agent_skills.setFixedHeight(92)
        self.agent_skills.setToolTip("Pinned skills are versioned procedures added to the "
                                     "agent's instructions; their tool needs are checked.")
        self.agent_instructions = QPlainTextEdit()
        self.agent_instructions.setPlaceholderText("Operating instructions (optional)")
        self.agent_instructions.setFixedHeight(90)
        form.addRow("Name", self.agent_name)
        form.addRow("Model", self.agent_model)
        form.addRow("Role", self.agent_role)
        form.addRow("", self.agent_write)
        form.addRow("", self.agent_shell)
        form.addRow("", self.agent_git)
        form.addRow("", self.agent_memory)
        form.addRow("Skills", self.agent_skills)
        form.addRow("Instructions", self.agent_instructions)
        form_card.body.addLayout(form)
        form_card.body.addWidget(button("Create draft agent", on_click=self._create_agent))
        form_card.body.addWidget(label(
            "A new agent starts as a draft. It can be activated only when its exact model "
            "digest has a qualification covering its tools.", "Faint", wrap=True))
        form_card.body.addStretch(1)
        layout.addWidget(form_card, 2)
        return tab

    def _selected_agent(self) -> str:
        current = self.agents_table.currentRow()
        rows = [current] if current >= 0 else []
        if not rows:
            return ""
        item = self.agents_table.item(rows[0], 0)
        return "" if item is None else str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _show_agent_detail(self) -> None:
        agent_id = self._selected_agent()
        row = next((item for item in self._agent_rows if item.agent_id == agent_id), None)
        if row is None:
            return
        agent = self.controller.service.get(agent_id)
        issues = "".join(f"<li>{html.escape(issue)}</li>" for issue in row.issues)
        self.agent_detail.setHtml(
            f"<p><b>{html.escape(row.name)}</b> v{row.latest_version} &mdash; "
            f"{html.escape(row.status)}</p>"
            f"<p>Model <code>{html.escape(agent.model_name)}</code> digest "
            f"<code>{html.escape(agent.model_digest[:16])}</code>; config "
            f"<code>{html.escape(agent.config_sha256[:16])}</code></p>"
            + (f"<p><b>Blocking issues</b></p><ul>{issues}</ul>" if issues
               else "<p>Ready to run.</p>")
            + f"<pre>{html.escape(json.dumps(agent.permissions.to_dict(), indent=1))}</pre>")

    def _create_agent(self) -> None:
        name = self.agent_name.text().strip()
        model = self.agent_model.currentText().strip()
        if not name or not model:
            self.status.setText("An agent needs a name and a model.")
            return
        self._start(
            f"Creating agent {name}", self.controller.create_agent,
            name=name, model=model, role=self.agent_role.currentData(),
            instructions=self.agent_instructions.toPlainText(),
            write=self.agent_write.isChecked(), shell=self.agent_shell.isChecked(),
            git=self.agent_git.isChecked(),
            skills=[self.agent_skills.item(index).data(Qt.ItemDataRole.UserRole)
                    for index in range(self.agent_skills.count())
                    if self.agent_skills.item(index).checkState() == Qt.CheckState.Checked],
            memory=self.agent_memory.isChecked(),
            on_result=lambda agent: self._after(
                f"Created draft agent {agent.name} as {agent.role_title}."))

    def _activate(self) -> None:
        agent_id = self._selected_agent()
        if agent_id:
            self._start("Activating agent", self.controller.activate, agent_id,
                        on_result=lambda agent: self._after(f"{agent.name} is active."))

    def _disable(self) -> None:
        agent_id = self._selected_agent()
        if agent_id:
            self._start("Disabling agent", self.controller.disable, agent_id,
                        on_result=lambda _none: self._after("Agent disabled."))

    # -- run tab ----------------------------------------------------------------------------
    def _run_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        form = QFormLayout()
        self.run_agent = QComboBox()
        self.run_workspace = QLineEdit(str(self.context.workspace_path()))
        browse = button("Browse", variant="Secondary", on_click=self._browse_workspace)
        workspace_row = QHBoxLayout()
        workspace_row.addWidget(self.run_workspace, 1)
        workspace_row.addWidget(browse)
        self.run_task = QPlainTextEdit()
        self.run_task.setPlaceholderText("What should the agent do?")
        self.run_task.setFixedHeight(100)
        self.run_checks = QPlainTextEdit()
        self.run_checks.setPlaceholderText(
            'Optional verification checks (JSON list), e.g. [{"kind": "file_exists", '
            '"path": "notes.md"}]')
        self.run_checks.setFixedHeight(70)
        form.addRow("Active agent", self.run_agent)
        form.addRow("Workspace", workspace_row)
        form.addRow("Task", self.run_task)
        form.addRow("Checks", self.run_checks)
        layout.addLayout(form)
        self.run_button = button("Run agent", on_click=self._run)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.run_button)
        layout.addLayout(row)
        self.run_log = QTextBrowser()
        layout.addWidget(self.run_log, 1)
        return tab

    def _browse_workspace(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Agent workspace",
                                                  self.run_workspace.text())
        if chosen:
            self.run_workspace.setText(chosen)

    def _run(self) -> None:
        agent_id = str(self.run_agent.currentData() or "")
        task = self.run_task.toPlainText().strip()
        if not agent_id or not task:
            self.status.setText("Choose an active agent and describe the task.")
            return
        checks: list[dict[str, Any]] | None = None
        raw_checks = self.run_checks.toPlainText().strip()
        if raw_checks:
            try:
                parsed = json.loads(raw_checks)
            except json.JSONDecodeError as exc:
                self.status.setText(f"Checks are not valid JSON: {exc}")
                return
            if not isinstance(parsed, list):
                self.status.setText("Checks must be a JSON list.")
                return
            checks = parsed
        self.run_log.clear()
        cancel = Event()
        self._start(
            "Running agent", self.controller.run_agent, agent_id, task,
            Path(self.run_workspace.text()).expanduser(), checks=checks,
            approval=lambda _description: False,  # unattended: overwrites/commands refused
            cancel_event=cancel, cancel=cancel, with_progress=True, event_kw="event",
            on_result=self._run_finished)

    def _run_finished(self, outcome: Any) -> None:
        result = outcome.result
        verdict = ("no checks requested" if outcome.report is None
                   else ("VERIFIED" if outcome.verified else
                         f"NOT VERIFIED ({', '.join(outcome.report.failure_reasons)})"))
        self.run_log.append(f"<hr><p><b>{html.escape(result.status.upper())}</b> after "
                            f"{result.steps} step(s); verification: {html.escape(verdict)}</p>"
                            f"<p>{html.escape(result.final_message)}</p>")
        self._after(f"Run {result.status}; {verdict}", refresh=False)

    # -- memory tab -------------------------------------------------------------------------
    def _memory_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 12, 0, 0)
        controls = QHBoxLayout()
        self.memory_scope = QComboBox()
        self.memory_scope.addItem("User", ("user", "default"))
        self.memory_scope.addItem("Current workspace", None)
        controls.addWidget(self.memory_scope)
        self.memory_query = QLineEdit()
        self.memory_query.setPlaceholderText("Search (semantic when an embedding model is set)")
        self.memory_query.returnPressed.connect(self._load_memory)
        controls.addWidget(self.memory_query, 1)
        controls.addWidget(button("Search", variant="Secondary", on_click=self._load_memory))
        controls.addWidget(button("Forget selected", variant="Secondary",
                                  on_click=self._forget_memory))
        controls.addWidget(button("Consolidate", variant="Secondary",
                                  on_click=self._consolidate_memory))
        layout.addLayout(controls)
        self.memory_mode = Pill("MEMORY", "muted")
        layout.addWidget(self.memory_mode, 0, Qt.AlignmentFlag.AlignLeft)
        self.memory_table = _table(["Category", "Content", "Source", "Confidence", "Flags",
                                    "Updated"])
        layout.addWidget(self.memory_table, 1)
        add = QHBoxLayout()
        self.memory_new = QLineEdit()
        self.memory_new.setPlaceholderText("Add a note to this scope")
        self.memory_category = QComboBox()
        self.memory_category.addItems(["fact", "decision", "preference", "constraint",
                                       "procedure", "lesson"])
        add.addWidget(self.memory_new, 1)
        add.addWidget(self.memory_category)
        add.addWidget(button("Remember", on_click=self._add_memory))
        layout.addLayout(add)
        return tab

    def _scopes(self) -> list[tuple[str, str]]:
        data = self.memory_scope.currentData()
        if data is None:
            return [("project", str(self.context.workspace_path().resolve()))]
        return [tuple(data)]  # type: ignore[list-item]

    def _load_memory(self) -> None:
        query = self.memory_query.text()
        self._start("Loading memory", self.controller.memory_rows, self._scopes(), query,
                    on_result=self._render_memory, quiet=True)

    def _render_memory(self, records: list[Any]) -> None:
        self._memory_rows = records
        self.memory_mode.setText(self.controller.memory_mode().upper())
        self.memory_table.setRowCount(len(records))
        for index, record in enumerate(records):
            values = [record.category, record.content, record.source,
                      f"{record.confidence:.2f}", ", ".join(record.flags),
                      format_timestamp(record.updated_at, short=True)]
            for column, value in enumerate(values):
                self.memory_table.setItem(index, column, _cell(value, record.memory_id))

    def _add_memory(self) -> None:
        text = self.memory_new.text().strip()
        if not text:
            return
        scope, scope_id = self._scopes()[0]
        self._start("Saving memory", self.controller.memory.remember, text, scope=scope,
                    scope_id=scope_id, category=self.memory_category.currentText(),
                    on_result=lambda _record: (self.memory_new.clear(), self._load_memory()))

    def _forget_memory(self) -> None:
        current = self.memory_table.currentRow()
        rows = [current] if current >= 0 else []
        if not rows:
            return
        item = self.memory_table.item(rows[0], 0)
        memory_id = str(item.data(Qt.ItemDataRole.UserRole)) if item else ""
        if memory_id:
            self._start("Forgetting note", self.controller.memory.forget, memory_id,
                        on_result=lambda _ok: self._load_memory())

    def _consolidate_memory(self) -> None:
        self._start("Consolidating memory", self.controller.memory.consolidate, self._scopes(),
                    on_result=lambda archived: (
                        self.status.setText(f"Archived {len(archived)} stale note(s)."),
                        self._load_memory()))

    # -- refresh ----------------------------------------------------------------------------
    def refresh(self) -> None:
        self._start("Loading agent foundry", self._load_all, on_result=self._render_all,
                    quiet=True)

    def _load_all(self) -> dict[str, Any]:
        controller = self.controller
        return {"models": controller.model_rows(), "agents": controller.agent_rows(),
                "skills": controller.skills()}

    def _render_all(self, data: dict[str, Any]) -> None:
        self._model_rows = data["models"]
        self._agent_rows = data["agents"]
        self.models_table.setRowCount(len(self._model_rows))
        self.agent_model.clear()
        for index, row in enumerate(self._model_rows):
            weak = ", ".join(key for key, value in row.suitability.items()
                             if value != "suitable")
            values = [row.name, format_bytes(row.size_bytes), row.qualification_status,
                      row.harness or "-", f"{row.passed}/{row.attempts}" if row.attempts else "-",
                      format_timestamp(row.qualified_at, short=True) if row.qualified_at else "-",
                      weak or "-"]
            for column, value in enumerate(values):
                self.models_table.setItem(index, column, _cell(value, row.digest))
            self.agent_model.addItem(row.name)
        checked = {self.agent_skills.item(index).data(Qt.ItemDataRole.UserRole)
                   for index in range(self.agent_skills.count())
                   if self.agent_skills.item(index).checkState() == Qt.CheckState.Checked}
        self.agent_skills.clear()
        for skill in data["skills"]:
            entry = QListWidgetItem(f"{skill['skill_id']} v{skill['version']} - "
                                    f"{skill.get('name', '')}")
            entry.setData(Qt.ItemDataRole.UserRole, skill["skill_id"])
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(Qt.CheckState.Checked if skill["skill_id"] in checked
                                else Qt.CheckState.Unchecked)
            self.agent_skills.addItem(entry)
        self.agents_table.setRowCount(len(self._agent_rows))
        self.run_agent.clear()
        for index, agent in enumerate(self._agent_rows):
            values = [agent.name, agent.status,
                      f"v{agent.active_version or '-'} / v{agent.latest_version}",
                      agent.model_name, agent.role_title,
                      "ready" if not agent.issues else f"{len(agent.issues)} issue(s)"]
            for column, value in enumerate(values):
                self.agents_table.setItem(index, column, _cell(value, agent.agent_id))
            if agent.status == "active" and not agent.issues:
                self.run_agent.addItem(f"{agent.name} ({agent.model_name})", agent.agent_id)
        qualified = sum(1 for row in self._model_rows if row.qualification_status
                        in {"qualified", "provisional"})
        self.status.setText(f"{qualified}/{len(self._model_rows)} installed models qualified; "
                            f"{len(self._agent_rows)} agent(s).")
        self._load_memory()

    # -- background work ----------------------------------------------------------------------
    _model_rows: list[Any] = []
    _agent_rows: list[Any] = []
    _memory_rows: list[Any] = []

    def _start(self, title: str, function: Any, *args: Any, on_result: Any,
               cancel: Event | None = None, with_progress: bool = False,
               event_kw: str = "", quiet: bool = False, **kwargs: Any) -> None:
        if not quiet:
            # Quiet loads (refresh, memory listing) never take the busy slot, so
            # they cannot clobber the cancel handle of a running qualification.
            if self._busy:
                self.status.setText("Another foundry operation is still running.")
                return
            self._busy = True
            self._cancel = cancel
            self.cancel_button.setEnabled(cancel is not None)
            self.status.setText(title + "...")
            self._activity_id = self.context.activity.begin(
                "AGENTS", title, cancel_event=cancel,
                cancel=(cancel.set if cancel is not None else None), page="agents")
        worker = FunctionWorker(lambda: None)
        emit = worker.signals.progress.emit
        if with_progress:
            if event_kw:
                kwargs[event_kw] = lambda item: emit(
                    f"[{getattr(item, 'kind', 'event')}] {getattr(item, 'message', item)}")
            else:
                kwargs["progress"] = emit
        worker.function, worker.args, worker.kwargs = function, args, kwargs
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(self._load_failed if quiet else self._failed)
        if not quiet:
            worker.signals.finished.connect(self._finish_busy)
        if with_progress:
            worker.signals.progress.connect(self._progress)
        self._workers.add(worker)
        worker.signals.finished.connect(lambda: self._workers.discard(worker))
        self.context.pool.start(worker)

    def _progress(self, message: str) -> None:
        self.status.setText(message[:160])
        if self.tabs.currentIndex() == 2:
            self.run_log.append(html.escape(message))
        if self._activity_id:
            self.context.activity.update(self._activity_id, message[:160])

    def _after(self, message: str, *, refresh: bool = True) -> None:
        self.status.setText(message)
        self.context.notify(message)
        if refresh:
            self.refresh()

    def _load_failed(self, message: str) -> None:
        first = message.strip().splitlines()[0] if message.strip() else "unknown error"
        self.status.setText(f"Could not load: {first[:400]}")

    def _failed(self, message: str) -> None:
        self.status.setText(message.strip().splitlines()[0][:600] if message.strip() else
                            "Operation failed.")
        self._finish_busy()

    def _finish_busy(self) -> None:
        self._busy = False
        self.cancel_button.setEnabled(False)
        self._cancel = None
        if self._activity_id:
            self.context.activity.finish(self._activity_id)
            self._activity_id = ""

    def _cancel_work(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.status.setText("Cancelling...")

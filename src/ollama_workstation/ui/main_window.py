"""Native desktop workstation window."""
from __future__ import annotations

import html
import json
from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.memory import MemoryStore
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.config import AppPaths, load_settings, save_settings
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.profiling.profiler import ModelProfiler
from ollama_workstation.profiling.research import HuggingFaceResearcher, attach_model_card_evidence
from ollama_workstation.storage.repository import Repository
from ollama_workstation.ui.workers import FunctionWorker

STYLE = """
QMainWindow, QWidget { background:#101318; color:#e8edf5; font-family:'Segoe UI'; }
QFrame#Sidebar { background:#171c24; border-right:1px solid #29313d; }
QLabel#Brand { font-size:17pt; font-weight:700; }
QLabel#Headline { font-size:19pt; font-weight:650; }
QLabel#Subtle { color:#98a5b7; }
QPushButton { background:#252e3a; border:1px solid #354254; border-radius:7px; padding:9px 13px; }
QPushButton:hover { background:#303c4b; }
QLineEdit, QListWidget, QPlainTextEdit, QTextBrowser { background:#171d26; border:1px solid #303b49; border-radius:7px; padding:7px; }
QProgressBar { background:#202833; border:0; border-radius:4px; height:8px; }
QProgressBar::chunk { background:#4b91e2; border-radius:4px; }
"""


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("11vated Intelligence — Ollama Agent Workstation")
        self.resize(1360, 860)
        self.paths = AppPaths.default()
        self.paths.ensure()
        self.settings = load_settings(self.paths.config_file)
        self.client = OllamaClient(self.settings.ollama_base_url, self.settings.request_timeout_seconds)
        self.profiler = ModelProfiler(self.client, self.settings.keep_alive)
        self.repository = Repository(self.paths.database_file)
        self.memory_store = MemoryStore(self.repository)
        self.pool = QThreadPool.globalInstance()
        self.models: list[str] = []
        self.selected_model = ""
        self.research_model_name = ""
        self._build()
        self.refresh_models()

    def _build(self) -> None:
        root_widget = QWidget()
        root = QHBoxLayout(root_widget)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        side = QVBoxLayout(sidebar)
        brand = QLabel("11VATED\nINTELLIGENCE")
        brand.setObjectName("Brand")
        side.addWidget(brand)
        tagline = QLabel("LOCAL AGENT WORKSTATION")
        tagline.setObjectName("Subtle")
        side.addWidget(tagline)
        self.status = QLabel("Checking Ollama…")
        side.addWidget(self.status)
        self.navigation = QListWidget()
        self.navigation.addItems(["Command Center", "Model Intelligence", "Agent Workspace", "Settings"])
        self.navigation.setCurrentRow(0)
        side.addWidget(self.navigation, 1)
        root.addWidget(sidebar)

        self.pages = QTabWidget()
        self.pages.tabBar().hide()
        self.pages.addTab(self._overview(), "Overview")
        self.pages.addTab(self._models(), "Models")
        self.pages.addTab(self._agent(), "Agent")
        self.pages.addTab(self._settings(), "Settings")
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        root.addWidget(self.pages, 1)
        self.setCentralWidget(root_widget)
        self.setStyleSheet(STYLE)

    def _overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Command Center")
        title.setObjectName("Headline")
        layout.addWidget(title)
        layout.addWidget(QLabel("Discover local models, profile them, and deploy evidence-backed agents."))
        self.overview = QTextBrowser()
        self.overview.setHtml("<h2>Evidence before identity</h2><p>Model titles and role recommendations are generated from documented origin data and observed capability tests.</p><h2>Local-first execution</h2><p>Inference goes to the configured Ollama endpoint. Remote model research is opt-in.</p><h2>Host-enforced permissions</h2><p>Agent tool requests are validated by the workstation, not trusted directly.</p>")
        layout.addWidget(self.overview, 1)
        return page

    def _models(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Model Intelligence")
        title.setObjectName("Headline")
        layout.addWidget(title)
        row = QHBoxLayout()
        refresh = QPushButton("Refresh inventory")
        refresh.clicked.connect(self.refresh_models)
        self.profile_button = QPushButton("Profile selected model")
        self.profile_button.clicked.connect(self.profile_selected)
        self.profile_button.setEnabled(False)
        row.addWidget(refresh)
        row.addWidget(self.profile_button)
        row.addWidget(QLabel("Trials"))
        self.profile_repeats = QSpinBox()
        self.profile_repeats.setRange(1, 5)
        self.profile_repeats.setValue(3)
        self.profile_repeats.setToolTip("Repeated smoke probes improve repeatability but are not certification.")
        row.addWidget(self.profile_repeats)
        self.research_button = QPushButton("Research model origin")
        self.research_button.clicked.connect(self.research_selected)
        self.research_button.setEnabled(self.settings.enable_remote_model_research)
        row.addWidget(self.research_button)
        layout.addLayout(row)
        split = QSplitter()
        self.model_list = QListWidget()
        self.model_list.currentTextChanged.connect(self.select_model)
        split.addWidget(self.model_list)
        self.profile_view = QTextBrowser()
        self.profile_view.setHtml("<h2>No model selected</h2>")
        split.addWidget(self.profile_view)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        self.profile_progress = QProgressBar()
        self.profile_progress.setRange(0, 0)
        self.profile_progress.hide()
        layout.addWidget(self.profile_progress)
        return page

    def _agent(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Agent Workspace")
        title.setObjectName("Headline")
        layout.addWidget(title)
        self.agent_model = QLineEdit()
        self.agent_model.setPlaceholderText("Exact installed Ollama model name")
        layout.addWidget(self.agent_model)
        self.task = QPlainTextEdit()
        self.task.setPlaceholderText("Describe a task for this agent…")
        self.task.setMaximumHeight(130)
        layout.addWidget(self.task)
        row = QHBoxLayout()
        choose = QPushButton("Choose workspace")
        choose.clicked.connect(self.choose_workspace)
        self.workspace = QLabel(str(self.paths.workspaces_dir))
        run = QPushButton("Run agent")
        run.clicked.connect(self.run_agent)
        remember = QPushButton("Add project memory")
        remember.clicked.connect(self.add_project_memory)
        manage_memory = QPushButton("Manage memory")
        manage_memory.clicked.connect(self.manage_project_memory)
        row.addWidget(choose)
        row.addWidget(self.workspace, 1)
        row.addWidget(remember)
        row.addWidget(manage_memory)
        row.addWidget(run)
        layout.addLayout(row)
        self.agent_output = QTextBrowser()
        layout.addWidget(self.agent_output, 1)
        self.agent_progress = QProgressBar()
        self.agent_progress.setRange(0, 0)
        self.agent_progress.hide()
        layout.addWidget(self.agent_progress)
        return page

    def _settings(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Settings")
        title.setObjectName("Headline")
        layout.addWidget(title)
        self.endpoint = QLineEdit(self.settings.ollama_base_url)
        layout.addWidget(QLabel("Ollama endpoint"))
        layout.addWidget(self.endpoint)
        self.remote_research = QCheckBox("Enable public Hugging Face model-card research (network access)")
        self.remote_research.setChecked(self.settings.enable_remote_model_research)
        layout.addWidget(self.remote_research)
        save = QPushButton("Save settings")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        layout.addStretch(1)
        return page

    def refresh_models(self) -> None:
        worker = FunctionWorker(self.profiler.inventory)
        worker.signals.result.connect(self.models_loaded)
        worker.signals.error.connect(self.show_error)
        self.pool.start(worker)

    def models_loaded(self, models: list[object]) -> None:
        self.models = [str(item.name) for item in models]
        self.model_list.clear()
        self.model_list.addItems(self.models)
        self.profile_button.setEnabled(bool(self.models))
        self.agent_model.setText(self.models[0] if self.models else "")
        self.status.setText(f"Ollama connected · {len(self.models)} installed models")
        self.overview.setHtml(f"<h2>Ollama connected</h2><p>{len(self.models)} local model(s) discovered.</p>")

    def select_model(self, name: str) -> None:
        self.profile_button.setEnabled(bool(name))
        self.selected_model = name
        profile = next((p for p in self.repository.list_profiles() if p.model_name == name), None)
        self.profile_view.setHtml(self.render_profile(profile) if profile else
                                  f"<h2>{html.escape(name)}</h2><p>No profile saved yet.</p>")

    def profile_selected(self) -> None:
        item = self.model_list.currentItem()
        if item is None:
            return
        name = item.text()
        self.profile_button.setEnabled(False)
        self.profile_progress.show()
        worker = FunctionWorker(self.profiler.profile, name, True, lambda message: worker.signals.progress.emit(message), self.profile_repeats.value())
        worker.signals.progress.connect(self.status.setText)
        worker.signals.result.connect(self.profile_ready)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(lambda: (self.profile_button.setEnabled(True), self.profile_progress.hide()))
        self.pool.start(worker)

    def profile_ready(self, profile: ModelProfile) -> None:
        self.repository.save_profile(profile)
        self.profile_view.setHtml(self.render_profile(profile))
        self.status.setText(f"Profiled {profile.model_name}: {profile.professional_title}")

    @staticmethod
    def render_profile(profile: ModelProfile) -> str:
        table = "".join(
            f"<tr><td>{html.escape(x.capability)}</td><td>{html.escape(x.status)}</td>"
            f"<td>{'—' if x.score is None else f'{x.score:.1f}/100'}</td>"
            f"<td>{x.passed}/{x.attempts}</td><td>{x.confidence:.2f}</td></tr>"
            for x in profile.capabilities)
        evidence = "".join(
            f"<li><b>{html.escape(x.claim)}</b>: {html.escape(x.value)} "
            f"[{html.escape(x.level)}] "
            f"{f'<a href={html.escape(x.source_url, quote=True)}>source</a>' if x.source_url else ''}"
            f"</li>" for x in profile.origin_evidence)
        roles = ", ".join(profile.recommended_roles) or "Not established"
        limitations = "".join(f"<li>{html.escape(item)}</li>" for item in profile.known_limitations)
        operational = html.escape(json.dumps(profile.operational, indent=2, ensure_ascii=False))
        rationale = "".join(f"<li>{html.escape(item)}</li>" for item in profile.identity_rationale)
        return (f"<h1>{html.escape(profile.professional_title)}</h1>"
                f"<h3>{html.escape(profile.model_name)}</h3>"
                f"<p>{html.escape(profile.primary_specialization)}</p>"
                f"<p><b>Publisher-declared purpose:</b> {html.escape(profile.declared_purpose)}</p>"
                f"<p><b>Identity confidence:</b> {profile.identity_confidence:.2f} "
                f"(provisional profile; not certification)</p>"
                f"<p><b>Agentability:</b> {html.escape(profile.agentability_mode)} / "
                f"{html.escape(profile.agentability_status)}</p>"
                f"<p><b>Recommended roles:</b> {html.escape(roles)}</p>"
                f"<h2>Why this identity?</h2><ul>{rationale}</ul>"
                f"<h2>Capability evidence</h2><table border='1' cellpadding='5'>"
                f"<tr><th>Capability</th><th>Status</th><th>Score</th><th>Passes</th><th>Confidence</th></tr>"
                f"{table}</table>"
                f"<h2>Origin and publisher evidence</h2><ul>{evidence}</ul>"
                f"<h2>Known limitations</h2><ul>{limitations or '<li>None recorded.</li>'}</ul>"
                f"<h2>Operational profile</h2><pre>{operational}</pre>")

    def research_selected(self) -> None:
        if not self.settings.enable_remote_model_research:
            QMessageBox.information(
                self, "Remote research disabled",
                "Enable public model-card research in Settings before searching Hugging Face."
            )
            return
        item = self.model_list.currentItem()
        if item is None:
            return
        self.research_model_name = item.text()
        self.research_button.setEnabled(False)
        self.profile_progress.show()
        worker = FunctionWorker(HuggingFaceResearcher().search_candidates, self.research_model_name)
        worker.signals.result.connect(self.research_candidates_ready)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(lambda: (
            self.research_button.setEnabled(True), self.profile_progress.hide()
        ))
        self.pool.start(worker)

    def research_candidates_ready(self, candidates: list[object]) -> None:
        if not candidates:
            QMessageBox.information(self, "No model-card matches", "No Hugging Face candidates were found.")
            return
        labels = [
            f"{candidate.repository_id} · {candidate.pipeline_tag or 'task undisclosed'} · "
            f"name match {candidate.match_score:.0%} (hint only) · {candidate.downloads:,} downloads"
            for candidate in candidates
        ]
        selected, accepted = QInputDialog.getItem(
            self, "Confirm upstream model identity",
            "Select the exact publisher repository for this Ollama model. "
            "Candidates are suggestions, not verified matches:",
            labels, 0, False
        )
        if not accepted:
            return
        candidate = candidates[labels.index(selected)]
        self.profile_progress.show()
        worker = FunctionWorker(HuggingFaceResearcher().fetch_model_card, candidate.repository_id)
        worker.signals.result.connect(self.model_card_ready)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(self.profile_progress.hide)
        self.pool.start(worker)

    def model_card_ready(self, card: dict[str, object]) -> None:
        profile = next(
            (item for item in self.repository.list_profiles()
             if item.model_name == self.research_model_name), None
        )
        if profile is None:
            QMessageBox.warning(self, "Profile required", "Profile this model before attaching its model card.")
            return
        attach_model_card_evidence(profile, card)
        self.repository.save_profile(profile)
        safe = self.research_model_name.replace("/", "_").replace(":", "_")
        markdown = str(card.get("model_card_markdown") or "")
        (self.paths.profiles_dir / f"{safe}.model-card.md").write_text(markdown, encoding="utf-8")
        if self.selected_model == self.research_model_name:
            self.profile_view.setHtml(self.render_profile(profile))
        self.status.setText(f"Publisher dossier attached: {profile.professional_title}")
        QMessageBox.information(
            self, "Model dossier updated",
            "Publisher disclosures, source provenance and model-card hash were attached. "
            "Empirical capability scores were not changed."
        )

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose agent workspace", str(self.paths.workspaces_dir))
        if selected:
            self.workspace.setText(selected)

    def add_project_memory(self) -> None:
        content, accepted = QInputDialog.getMultiLineText(
            self, "Add project memory", "Save a durable note for this workspace:"
        )
        if not accepted or not content.strip():
            return
        categories = ["fact", "decision", "preference", "constraint", "procedure", "lesson"]
        category, accepted = QInputDialog.getItem(
            self, "Memory category", "Classify this note:", categories, 0, False
        )
        if not accepted:
            return
        tags_text, _ = QInputDialog.getText(
            self, "Memory tags", "Optional comma-separated retrieval tags:"
        )
        try:
            scope_id = str(Path(self.workspace.text()).expanduser().resolve())
            record = self.memory_store.remember(
                content, scope="project", scope_id=scope_id, category=category,
                tags=[tag.strip() for tag in tags_text.split(",") if tag.strip()],
                source="user",
            )
            self.status.setText(f"Saved project memory: {record.memory_id[:8]}")
        except (OSError, ValueError, RuntimeError) as exc:
            self.show_error(str(exc))

    def manage_project_memory(self) -> None:
        scope_id = str(Path(self.workspace.text()).expanduser().resolve())
        records = self.memory_store.list([("project", scope_id)], limit=100)
        if not records:
            QMessageBox.information(self, "Project memory", "No saved notes exist for this workspace.")
            return
        labels = [
            f"{item.memory_id[:8]} · {item.category} · {item.content[:110].replace(chr(10), ' ')}"
            for item in records
        ]
        selected, accepted = QInputDialog.getItem(
            self, "Manage project memory", "Select a note to archive:", labels, 0, False
        )
        if not accepted:
            return
        record = records[labels.index(selected)]
        confirm = QMessageBox.question(
            self, "Archive memory", "Archive the selected note? It will no longer be retrieved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.memory_store.forget(record.memory_id)
            self.status.setText(f"Archived memory: {record.memory_id[:8]}")

    def run_agent(self) -> None:
        model = self.agent_model.text().strip()
        task = self.task.toPlainText().strip()
        profile = next((p for p in self.repository.list_profiles() if p.model_name == model), None)
        if not task or profile is None:
            QMessageBox.warning(self, "Profile required", "Enter a task and profile the selected model first.")
            return
        workspace = Path(self.workspace.text())
        tools = WorkspaceTools(workspace, allow_shell=False)
        runtime = AgentRuntime(self.client, max_steps=self.settings.max_agent_steps,
                               keep_alive=self.settings.keep_alive, repository=self.repository,
                               memory_store=self.memory_store)
        self.agent_progress.show()
        worker = FunctionWorker(runtime.run, model, profile, task, tools)
        worker.signals.progress.connect(lambda event: self.agent_output.append(html.escape(str(event))))
        worker.signals.result.connect(self.agent_finished)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(self.agent_progress.hide)
        self.pool.start(worker)

    def agent_finished(self, result: object) -> None:
        self.agent_output.append(f"<b>{html.escape(result.status)}</b><p>{html.escape(result.final_message)}</p>")

    def save(self) -> None:
        self.settings.ollama_base_url = self.endpoint.text().strip().rstrip("/")
        self.settings.enable_remote_model_research = self.remote_research.isChecked()
        self.research_button.setEnabled(self.settings.enable_remote_model_research)
        save_settings(self.paths.config_file, self.settings)
        self.client = OllamaClient(self.settings.ollama_base_url, self.settings.request_timeout_seconds)
        self.profiler = ModelProfiler(self.client, self.settings.keep_alive)
        QMessageBox.information(self, "Saved", "Settings saved. Refresh model inventory to reconnect.")

    def show_error(self, message: str) -> None:
        self.status.setText(message[:180])
        QMessageBox.critical(self, "Operation failed", message)


def run_desktop() -> int:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    return app.exec()

"""Native desktop workstation window."""
from __future__ import annotations
import html
from pathlib import Path
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPushButton, QPlainTextEdit,
    QProgressBar, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.config import AppPaths, load_settings, save_settings
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.profiling.profiler import ModelProfiler
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
        self.pool = QThreadPool.globalInstance()
        self.models: list[str] = []
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
        row.addWidget(choose)
        row.addWidget(self.workspace, 1)
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
        worker = FunctionWorker(self.profiler.profile, name)
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
            f"<td>{'—' if x.score is None else f'{x.score:.0f}/100'}</td></tr>"
            for x in profile.capabilities)
        evidence = "".join(f"<li>{html.escape(x.claim)}: {html.escape(x.value)} [{html.escape(x.level)}]</li>"
                           for x in profile.origin_evidence)
        return (f"<h1>{html.escape(profile.professional_title)}</h1>"
                f"<h3>{html.escape(profile.model_name)}</h3>"
                f"<p>{html.escape(profile.primary_specialization)}</p>"
                f"<p>Agentability: {html.escape(profile.agentability_mode)} / {html.escape(profile.agentability_status)}</p>"
                f"<h2>Capabilities</h2><table border='1' cellpadding='5'>{table}</table>"
                f"<h2>Origin evidence</h2><ul>{evidence}</ul>")

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Choose agent workspace", str(self.paths.workspaces_dir))
        if selected:
            self.workspace.setText(selected)

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
                               keep_alive=self.settings.keep_alive, repository=self.repository)
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

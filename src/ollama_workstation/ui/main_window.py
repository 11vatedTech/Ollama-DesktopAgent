"""Native desktop workstation window."""
from __future__ import annotations

import html
import json
from collections.abc import Callable
from pathlib import Path
from threading import Event

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

from ollama_workstation.agents.binding import validate_installed_model_binding
from ollama_workstation.agents.memory import MemoryStore
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.benchmarking.runner import BenchmarkRunner
from ollama_workstation.config import AppPaths, load_settings, save_settings
from ollama_workstation.models.schemas import ModelProfile, ModelRef
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


def _search_candidates_for_model(model_name: str) -> tuple[str, list[object]]:
    """Search publisher candidates and retain the originating local model name."""
    return model_name, HuggingFaceResearcher().search_candidates(model_name)


def _fetch_card_for_model(model_name: str, repository_id: str) -> tuple[str, dict[str, object]]:
    """Fetch a model card together with the model name that initiated the request."""
    return model_name, HuggingFaceResearcher().fetch_model_card(repository_id)


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
        self.agent_cancel_event: Event | None = None
        self.models: list[str] = []
        self.model_refs: dict[str, ModelRef] = {}
        self.selected_model = ""
        self.run_history_data: list[dict[str, object]] = []
        self._build()
        self.refresh_models()
        self.refresh_runs()

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
        self.navigation.addItems([
            "Command Center", "Model Intelligence", "Agent Workspace", "Run History", "Settings"
        ])
        self.navigation.setCurrentRow(0)
        side.addWidget(self.navigation, 1)
        root.addWidget(sidebar)

        self.pages = QTabWidget()
        self.pages.tabBar().hide()
        self.pages.addTab(self._overview(), "Overview")
        self.pages.addTab(self._models(), "Models")
        self.pages.addTab(self._agent(), "Agent")
        self.pages.addTab(self._run_history(), "Run History")
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
        self.benchmark_button = QPushButton("Run core benchmark")
        self.benchmark_button.clicked.connect(self.benchmark_selected)
        self.benchmark_button.setEnabled(False)
        row.addWidget(refresh)
        row.addWidget(self.profile_button)
        row.addWidget(self.benchmark_button)
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
        self.profile_view.setOpenExternalLinks(True)
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
        self.run_agent_button = QPushButton("Run agent")
        self.run_agent_button.clicked.connect(self.run_agent)
        self.cancel_agent_button = QPushButton("Stop agent")
        self.cancel_agent_button.clicked.connect(self.cancel_agent)
        self.cancel_agent_button.setEnabled(False)
        remember = QPushButton("Add project memory")
        remember.clicked.connect(self.add_project_memory)
        manage_memory = QPushButton("Manage memory")
        manage_memory.clicked.connect(self.manage_project_memory)
        row.addWidget(choose)
        row.addWidget(self.workspace, 1)
        row.addWidget(remember)
        row.addWidget(manage_memory)
        row.addWidget(self.run_agent_button)
        row.addWidget(self.cancel_agent_button)
        layout.addLayout(row)
        self.agent_output = QTextBrowser()
        layout.addWidget(self.agent_output, 1)
        self.agent_progress = QProgressBar()
        self.agent_progress.setRange(0, 0)
        self.agent_progress.hide()
        layout.addWidget(self.agent_progress)
        return page

    def _run_history(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Run History")
        title.setObjectName("Headline")
        layout.addWidget(title)
        toolbar = QHBoxLayout()
        refresh = QPushButton("Refresh history")
        refresh.clicked.connect(self.refresh_runs)
        toolbar.addWidget(refresh)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        split = QSplitter()
        self.run_history_list = QListWidget()
        self.run_history_list.currentRowChanged.connect(self.show_run)
        split.addWidget(self.run_history_list)
        self.run_history_detail = QTextBrowser()
        self.run_history_detail.setHtml(
            "<h2>Execution journal</h2><p>Select a run to inspect its durable event history.</p>"
        )
        split.addWidget(self.run_history_detail)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        return page

    def refresh_runs(self) -> None:
        """Reload durable run summaries without blocking on provider or model work."""
        if not hasattr(self, "run_history_list"):
            return
        try:
            self.run_history_data = self.repository.list_runs(100)
            self.run_history_list.clear()
            for run in self.run_history_data:
                task = " ".join(str(run.get("task") or "").split())
                if len(task) > 72:
                    task = task[:69] + "..."
                self.run_history_list.addItem(
                    f"{run.get('status', 'unknown').upper()} · "
                    f"{run.get('agent_title', 'Agent')} · "
                    f"{str(run.get('created_at', ''))[:19]} · {task}"
                )
            if self.run_history_data:
                self.run_history_list.setCurrentRow(0)
            else:
                self.run_history_detail.setHtml(
                    "<h2>No agent runs yet</h2><p>Completed, failed and interrupted executions will appear here.</p>"
                )
        except Exception as exc:
            self.run_history_detail.setHtml(
                f"<h2>Unable to load run history</h2><p>{html.escape(str(exc))}</p>"
            )

    def show_run(self, row: int) -> None:
        """Render a selected run and its ordered, durable event journal."""
        if row < 0 or row >= len(self.run_history_data):
            return
        run = self.run_history_data[row]
        run_id = str(run.get("run_id") or "")
        try:
            events = self.repository.list_run_events(run_id)
        except Exception:
            events = []
        if not events:
            events = [
                {
                    "sequence": index + 1,
                    "kind": item.get("kind", "event"),
                    "message": item.get("message", ""),
                    "timestamp": item.get("timestamp", ""),
                }
                for index, item in enumerate(run.get("trace", []))
                if isinstance(item, dict)
            ]
        event_rows = []
        for event in events:
            message, _ = WorkspaceTools.redact_sensitive_text(str(event.get("message") or ""))
            event_rows.append(
                f"<article><h3>#{int(event.get('sequence', 0)):03d} · "
                f"{html.escape(str(event.get('kind', 'event')))}</h3>"
                f"<p><small>{html.escape(str(event.get('timestamp') or ''))}</small></p>"
                f"<p>{html.escape(message)}</p></article>"
            )
        task, _ = WorkspaceTools.redact_sensitive_text(str(run.get("task") or ""))
        self.run_history_detail.setHtml(
            f"<h1>{html.escape(str(run.get('agent_title') or 'Agent'))}</h1>"
            f"<p><b>Status:</b> {html.escape(str(run.get('status') or 'unknown'))}</p>"
            f"<p><b>Model:</b> {html.escape(str(run.get('model_name') or ''))}</p>"
            f"<p><b>Run ID:</b> {html.escape(run_id)}</p>"
            f"<p><b>Started:</b> {html.escape(str(run.get('created_at') or ''))}</p>"
            f"<p><b>Finished:</b> {html.escape(str(run.get('completed_at') or 'Still running'))}</p>"
            f"<h2>Objective</h2><p>{html.escape(task)}</p>"
            f"<h2>Event journal ({len(events)} events)</h2>{''.join(event_rows)}"
        )

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
        self.model_refs = {
            str(item.name): item for item in models if isinstance(item, ModelRef)
        }
        self.model_list.clear()
        self.model_list.addItems(self.models)
        self.profile_button.setEnabled(bool(self.models))
        self.benchmark_button.setEnabled(bool(self.models))
        self.agent_model.setText(self.models[0] if self.models else "")
        self.status.setText(f"Ollama connected · {len(self.models)} installed models")
        self.overview.setHtml(f"<h2>Ollama connected</h2><p>{len(self.models)} local model(s) discovered.</p>")

    def select_model(self, name: str) -> None:
        self.profile_button.setEnabled(bool(name))
        self.benchmark_button.setEnabled(bool(name) and name in self.model_refs)
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

    def benchmark_selected(self) -> None:
        item = self.model_list.currentItem()
        if item is None:
            return
        name = item.text()
        model_ref = self.model_refs.get(name)
        if model_ref is None:
            QMessageBox.warning(
                self, "Model inventory unavailable",
                "Refresh the Ollama inventory and select an installed model before benchmarking.",
            )
            return
        self.benchmark_button.setEnabled(False)
        self.profile_progress.show()
        runner = BenchmarkRunner(self.client, keep_alive=self.settings.keep_alive)
        worker = FunctionWorker(
            runner.run, name,
            model_digest=model_ref.digest,
            suite_id="core",
            repeats=self.profile_repeats.value(),
            progress=lambda message: worker.signals.progress.emit(message),
        )
        worker.signals.progress.connect(self.status.setText)
        worker.signals.result.connect(self.benchmark_ready)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(lambda: (
            self.benchmark_button.setEnabled(True), self.profile_progress.hide()
        ))
        self.pool.start(worker)

    def benchmark_ready(self, report: object) -> None:
        try:
            destination = (
                self.paths.profiles_dir / "benchmarks"
                / f"{report.model_name.replace('/', '_').replace(':', '_')}-{report.run_id[:8]}.json"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
            )
            rows = "".join(
                "<tr>"
                f"<td>{html.escape(case.capability)}</td>"
                f"<td>{case.passed}/{case.attempts}</td>"
                f"<td>{case.score:.1f}/100</td>"
                f"<td>{case.wilson_95_low:.2f}–{case.wilson_95_high:.2f}</td>"
                f"<td>{'—' if case.median_latency_ms is None else f'{case.median_latency_ms:.0f} ms'}</td>"
                "</tr>"
                for case in report.cases
            )
            safe_model = html.escape(report.model_name)
            self.profile_view.setHtml(
                f"<h1>11vated Core Benchmark</h1><h3>{safe_model}</h3>"
                f"<p><b>Suite:</b> {html.escape(report.suite_id)}@{html.escape(report.suite_version)}"
                f" · <b>Digest:</b> {html.escape(report.model_digest or 'unknown')}</p>"
                f"<h2>{report.score:.1f}/100</h2>"
                f"<p>{report.total_passed}/{report.total_attempts} trial(s) passed across "
                f"{len(report.cases)} deterministic cases. Results are diagnostic, not certification.</p>"
                "<table border='1' cellpadding='5'>"
                "<tr><th>Capability</th><th>Passes</th><th>Score</th><th>Wilson 95% interval</th>"
                f"<th>Median latency</th></tr>{rows}</table>"
                f"<p><b>Manifest SHA-256:</b> {html.escape(report.manifest_sha256)}</p>"
                f"<p><b>Report:</b> {html.escape(str(destination))}</p>"
            )
            self.status.setText(
                f"Benchmark completed · {report.score:.1f}/100 · "
                f"{report.total_passed}/{report.total_attempts} attempts passed"
            )
        except (OSError, TypeError, ValueError, AttributeError) as exc:
            self.show_error(f"Benchmark completed but report rendering failed: {exc}")

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
        evidence_items = []
        for item in profile.origin_evidence:
            source_link = ""
            if item.source_url:
                safe_url = html.escape(item.source_url, quote=True)
                source_link = f'<a href="{safe_url}">source</a>'
            evidence_items.append(
                f"<li><b>{html.escape(item.claim)}</b>: {html.escape(item.value)} "
                f"[{html.escape(item.level)}] {source_link}</li>"
            )
        evidence = "".join(evidence_items)
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
        """Start opt-in model-card lookup for the exact model currently selected."""
        if not self.settings.enable_remote_model_research:
            QMessageBox.information(
                self, "Remote research disabled",
                "Enable public model-card research in Settings before searching Hugging Face."
            )
            return
        item = self.model_list.currentItem()
        if item is None:
            return
        model_name = item.text()
        self.research_button.setEnabled(False)
        self.profile_progress.show()
        worker = FunctionWorker(_search_candidates_for_model, model_name)
        worker.signals.result.connect(self.research_candidates_ready)
        worker.signals.error.connect(self.research_failed)
        worker.signals.finished.connect(self.profile_progress.hide)
        self.pool.start(worker)

    def research_failed(self, message: str) -> None:
        """Report a failed research operation and restore its controls."""
        self.research_button.setEnabled(self.settings.enable_remote_model_research)
        self.profile_progress.hide()
        self.show_error(message)

    def research_candidates_ready(self, result: tuple[str, list[object]]) -> None:
        """Ask the user to confirm an upstream repository for the captured model."""
        model_name, candidates = result
        if not candidates:
            self.research_button.setEnabled(self.settings.enable_remote_model_research)
            QMessageBox.information(self, "No model-card matches", "No Hugging Face candidates were found.")
            return
        labels = [
            f"{candidate.repository_id} · {candidate.pipeline_tag or 'task undisclosed'} · "
            f"name match {candidate.match_score:.0%} (hint only) · {candidate.downloads:,} downloads"
            for candidate in candidates
        ]
        selected, accepted = QInputDialog.getItem(
            self, "Confirm upstream model identity",
            f"Select the exact publisher repository for {model_name}. "
            "Candidates are suggestions, not verified matches:",
            labels, 0, False
        )
        if not accepted:
            self.research_button.setEnabled(self.settings.enable_remote_model_research)
            return
        candidate = candidates[labels.index(selected)]
        self.profile_progress.show()
        worker = FunctionWorker(_fetch_card_for_model, model_name, candidate.repository_id)
        worker.signals.result.connect(self.model_card_ready)
        worker.signals.error.connect(self.research_failed)
        worker.signals.finished.connect(lambda: (
            self.profile_progress.hide(),
            self.research_button.setEnabled(self.settings.enable_remote_model_research),
        ))
        self.pool.start(worker)

    def model_card_ready(self, result: tuple[str, dict[str, object]]) -> None:
        """Attach a retrieved publisher card to the profile for its captured model."""
        model_name, card = result
        profile = next(
            (item for item in self.repository.list_profiles()
             if item.model_name == model_name), None
        )
        if profile is None:
            QMessageBox.warning(self, "Profile required", "Profile this model before attaching its model card.")
            return
        attach_model_card_evidence(profile, card)
        self.repository.save_profile(profile)
        safe = model_name.replace("/", "_").replace(":", "_")
        markdown = str(card.get("model_card_markdown") or "")
        (self.paths.profiles_dir / f"{safe}.model-card.md").write_text(markdown, encoding="utf-8")
        if self.selected_model == model_name:
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
        """Run a profile-bound agent off the UI thread with cooperative cancellation."""
        model = self.agent_model.text().strip()
        task = self.task.toPlainText().strip()
        profile = next((p for p in self.repository.list_profiles() if p.model_name == model), None)
        if not task or profile is None:
            QMessageBox.warning(self, "Profile required", "Enter a task and profile the selected model first.")
            return
        workspace = Path(self.workspace.text()).expanduser().resolve()
        cancel_event = Event()
        self.agent_cancel_event = cancel_event
        self.run_agent_button.setEnabled(False)
        self.cancel_agent_button.setEnabled(True)
        self.agent_progress.show()
        self.agent_output.clear()
        worker = FunctionWorker(
            lambda: self._run_agent_task(
                model, profile, task, workspace, cancel_event,
                lambda message: worker.signals.progress.emit(message),
            )
        )
        worker.signals.progress.connect(
            lambda message: self.agent_output.append(html.escape(message))
        )
        worker.signals.result.connect(self.agent_finished)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(lambda: (
            self.agent_progress.hide(),
            self.run_agent_button.setEnabled(True),
            self.cancel_agent_button.setEnabled(False),
            setattr(self, "agent_cancel_event", None),
        ))
        self.pool.start(worker)

    def cancel_agent(self) -> None:
        """Request cooperative cancellation of the active agent run."""
        if self.agent_cancel_event is None:
            return
        self.agent_cancel_event.set()
        self.cancel_agent_button.setEnabled(False)
        self.status.setText(
            "Stop requested. The active Ollama request or tool operation may finish before it is honored."
        )
        self.agent_output.append("Stop requested; waiting for the current operation boundary.")

    def _run_agent_task(
        self, model: str, profile: ModelProfile, task: str,
        workspace: Path, cancel_event: Event, progress: Callable[[str], None],
    ) -> object:
        """Validate the live model digest and run the bounded agent with local memory."""
        installed = next(
            (item for item in ModelProfiler(self.client).inventory() if item.name == model),
            None,
        )
        if installed is None:
            raise ValueError(f"Model {model!r} is not installed in the configured Ollama instance.")
        validate_installed_model_binding(profile, installed)
        tools = WorkspaceTools(workspace, allow_shell=False, cancel_event=cancel_event)
        runtime = AgentRuntime(
            self.client, max_steps=self.settings.max_agent_steps,
            keep_alive=self.settings.keep_alive, repository=self.repository,
            memory_store=self.memory_store,
        )
        return runtime.run(
            model, profile, task, tools,
            event=lambda item: progress(f"[{item.kind}] {item.message}"),
            cancel_event=cancel_event,
        )

    def agent_finished(self, result: object) -> None:
        safe_message, redacted_count = WorkspaceTools.redact_sensitive_text(result.final_message)
        notice = (
            f"<p><small>Security filter redacted {redacted_count} credential-like value(s).</small></p>"
            if redacted_count else ""
        )
        self.agent_output.append(
            f"<b>{html.escape(result.status)}</b>{notice}<p>{html.escape(safe_message)}</p>"
        )
        self.refresh_runs()

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

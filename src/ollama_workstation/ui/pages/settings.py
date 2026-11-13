"""Settings: runtime, policy, appearance and MCP configuration."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.mcp_gateway import MCPServerSpec
from ollama_workstation.config import save_settings
from ollama_workstation.ui.components import (
    Card,
    PageHeader,
    SectionHeader,
    button,
    divider,
    label,
)
from ollama_workstation.ui.state import AppContext


class SettingsPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._build()
        self._load()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 0)
        root.setSpacing(14)
        self.save_button = button("Save configuration", variant="Primary", on_click=self.save)
        root.addWidget(PageHeader(
            "SYSTEM / CONFIGURATION",
            "Settings",
            "Control the local runtime, tool policy, visual motion and approved integrations.",
            actions=[self.save_button],
        ))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("PageBody")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 26)
        layout.setSpacing(14)
        layout.addWidget(self._runtime_card())
        layout.addWidget(self._policy_card())
        layout.addWidget(self._appearance_card())
        layout.addWidget(self._mcp_card())
        layout.addWidget(self._paths_card())
        layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _runtime_card(self) -> QWidget:
        card = Card(margins=(18, 15, 18, 15))
        card.body.addWidget(SectionHeader(
            "Local runtime",
            "Inference remains on the configured Ollama endpoint.",
        ))
        card.body.addWidget(divider())
        form = QFormLayout()
        form.setSpacing(10)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("http://127.0.0.1:11434")
        form.addRow("Ollama endpoint", self.endpoint)
        self.timeout = QLineEdit()
        self.timeout.setValidator(QIntValidator(10, 600, self.timeout))
        self.timeout.setPlaceholderText("120")
        self.timeout.setToolTip("Allowed range: 10–600 seconds")
        form.addRow("Request timeout (seconds)", self.timeout)
        self.max_steps = QLineEdit()
        self.max_steps.setValidator(QIntValidator(1, 40, self.max_steps))
        self.max_steps.setPlaceholderText("12")
        self.max_steps.setToolTip("Allowed range: 1–40 steps")
        form.addRow("Agent step budget", self.max_steps)
        self.keep_alive = QLineEdit()
        self.keep_alive.setPlaceholderText("5m")
        form.addRow("Model keep-alive", self.keep_alive)
        self.workspace = QLineEdit()
        self.workspace.setPlaceholderText(str(self.context.workspace_path()))
        workspace_row = QWidget()
        workspace_layout = QHBoxLayout(workspace_row)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(self.workspace, 1)
        workspace_layout.addWidget(button("Choose", variant="Ghost", on_click=self.choose_workspace))
        form.addRow("Workspace root", workspace_row)
        card.body.addLayout(form)
        return card

    def _policy_card(self) -> QWidget:
        card = Card(margins=(18, 15, 18, 15))
        card.body.addWidget(SectionHeader(
            "Policy and privacy",
            "The host enforces these boundaries before a tool call reaches a model.",
        ))
        card.body.addWidget(divider())
        self.remote_research = QCheckBox(
            "Enable opt-in Hugging Face model-card research (network access)"
        )
        card.body.addWidget(self.remote_research)
        self.allow_unqualified = QCheckBox(
            "Operator override: run models that have no harness qualification (not recommended)"
        )
        card.body.addWidget(self.allow_unqualified)
        embedding_row = QHBoxLayout()
        embedding_row.addWidget(label("Memory embedding model", "Muted"))
        self.embedding_model = QLineEdit()
        self.embedding_model.setPlaceholderText('"auto", a model name, or empty for keyword-only')
        embedding_row.addWidget(self.embedding_model, 1)
        card.body.addLayout(embedding_row)
        self.shell_note = label(
            "Chat and workforce agents never run commands. Agents created in the Agent "
            "Foundry may run Python only inside a Windows Job Object (process, memory and "
            "lifetime limits); that is not a filesystem or network sandbox. Workspace tools "
            "are path-contained and MCP grants are exact-name only.",
            "Muted", wrap=True,
        )
        card.body.addWidget(self.shell_note)
        return card

    def _appearance_card(self) -> QWidget:
        card = Card(margins=(18, 15, 18, 15))
        card.body.addWidget(SectionHeader(
            "Motion and accessibility",
            "The interface remains fully usable with motion reduced to a minimum.",
        ))
        card.body.addWidget(divider())
        self.reduced_motion = QCheckBox("Reduce motion and pause continuous animation")
        card.body.addWidget(self.reduced_motion)
        row = QVBoxLayout()
        row.addWidget(label("Animation intensity", "MetricLabel"))
        self.intensity = QSlider(Qt.Orientation.Horizontal)
        self.intensity.setRange(20, 100)
        self.intensity.setTickInterval(10)
        self.intensity.setTickPosition(QSlider.TickPosition.NoTicks)
        row.addWidget(self.intensity)
        self.intensity_value = label("80%", "Muted")
        row.addWidget(self.intensity_value)
        card.body.addLayout(row)
        self.intensity.valueChanged.connect(lambda value: self.intensity_value.setText(f"{value}%"))
        self.reduced_motion.toggled.connect(self._preview_motion)
        return card

    def _mcp_card(self) -> QWidget:
        card = Card(margins=(18, 15, 18, 15))
        card.body.addWidget(SectionHeader(
            "MCP servers",
            "Exact tool grants only. Secrets are referenced by host environment variable name.",
        ))
        card.body.addWidget(divider())
        self.mcp_config = QPlainTextEdit()
        self.mcp_config.setMinimumHeight(210)
        self.mcp_config.setPlaceholderText(
            '[{"name":"filesystem","transport":"stdio","enabled":false,'
            '"command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","C:/workspace"],'
            '"approved_tools":["read_file","list_directory"]}]'
        )
        card.body.addWidget(self.mcp_config)
        card.body.addWidget(label(
            "Configuration is validated before it is saved. A wildcard grant, embedded secret "
            "or malformed transport is rejected.",
            "Faint", wrap=True,
        ))
        return card

    def _paths_card(self) -> QWidget:
        card = Card(margins=(18, 15, 18, 15))
        card.body.addWidget(SectionHeader("Local paths", "Data stays under the application directory."))
        card.body.addWidget(divider())
        for name, value in (
            ("Application data", str(self.context.paths.root)),
            ("SQLite database", str(self.context.paths.database_file)),
            ("Profiles", str(self.context.paths.profiles_dir)),
            ("Structured logs", str(self.context.paths.logs_dir)),
        ):
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 2, 0, 2)
            layout.addWidget(label(name, "Faint"))
            layout.addWidget(label(value, "Mono", wrap=True), 1)
            card.body.addWidget(row)
        return card

    def _load(self) -> None:
        settings = self.context.settings
        self.endpoint.setText(settings.ollama_base_url)
        self.timeout.setText(str(int(settings.request_timeout_seconds)))
        self.max_steps.setText(str(settings.max_agent_steps))
        self.keep_alive.setText(settings.keep_alive)
        # Show the effective directory when no explicit override is configured.
        # A blank field previously looked like a lost workspace rather than the
        # documented application default.
        self.workspace.setText(
            settings.workspace_root.strip() or str(self.context.workspace_path())
        )
        self.remote_research.setChecked(settings.enable_remote_model_research)
        self.allow_unqualified.setChecked(settings.allow_unqualified_models)
        self.embedding_model.setText(settings.memory_embedding_model)
        self.reduced_motion.setChecked(settings.reduced_motion)
        self.intensity.setValue(int(settings.animation_intensity * 100))
        self.mcp_config.setPlainText(json.dumps(settings.mcp_servers, indent=2, ensure_ascii=False))

    def choose_workspace(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose workspace root", str(self.context.workspace_path())
        )
        if selected:
            self.workspace.setText(selected)

    def _preview_motion(self, checked: bool) -> None:
        previous = self.context.settings.reduced_motion
        self.context.settings.reduced_motion = checked
        self.context.events.theme_changed.emit()
        if not checked:
            self.context.settings.reduced_motion = previous

    def save(self) -> None:
        try:
            raw_servers = json.loads(self.mcp_config.toPlainText() or "[]")
            if not isinstance(raw_servers, list) or not all(isinstance(item, dict) for item in raw_servers):
                raise ValueError("MCP configuration must be a JSON array of objects.")
            specs = [MCPServerSpec.from_dict(item) for item in raw_servers]
            if len({spec.name for spec in specs}) != len(specs):
                raise ValueError("MCP server names must be unique.")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Invalid MCP configuration", str(exc))
            return
        endpoint = self.endpoint.text().strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            QMessageBox.warning(self, "Invalid endpoint", "Ollama endpoint must be an HTTP(S) URL.")
            return
        workspace = self.workspace.text().strip()
        if workspace and not Path(workspace).expanduser().is_dir():
            QMessageBox.warning(self, "Workspace missing", "Choose an existing workspace directory.")
            return
        try:
            timeout = int(self.timeout.text().strip() or "120")
            max_steps = int(self.max_steps.text().strip() or "12")
        except ValueError:
            QMessageBox.warning(self, "Invalid numeric value", "Timeout and step budget must be integers.")
            return
        if not 10 <= timeout <= 600:
            QMessageBox.warning(self, "Invalid timeout", "Request timeout must be 10–600 seconds.")
            return
        if not 1 <= max_steps <= 40:
            QMessageBox.warning(self, "Invalid step budget", "Agent step budget must be 1–40.")
            return
        settings = self.context.settings
        settings.ollama_base_url = endpoint
        settings.request_timeout_seconds = float(timeout)
        settings.max_agent_steps = max_steps
        settings.keep_alive = self.keep_alive.text().strip() or "5m"
        settings.workspace_root = workspace
        settings.enable_remote_model_research = self.remote_research.isChecked()
        settings.allow_unqualified_models = self.allow_unqualified.isChecked()
        settings.memory_embedding_model = self.embedding_model.text().strip()
        settings.reduced_motion = self.reduced_motion.isChecked()
        settings.animation_intensity = self.intensity.value() / 100.0
        settings.mcp_servers = raw_servers
        save_settings(self.context.paths.config_file, settings)
        self.context.refresh_settings()
        self.context.notify("Configuration saved locally.")
        self.context.events.data_changed.emit("settings")
        self.context.set_state("complete", "Configuration saved")
        QMessageBox.information(self, "Settings saved", "The local configuration was saved.")

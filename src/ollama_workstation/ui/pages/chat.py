"""Advanced Chat: model, agent, routed and workforce communication in one surface."""

from __future__ import annotations

import base64
import mimetypes
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeyEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agentification.gate import clear_repository_run
from ollama_workstation.agents.binding import validate_installed_model_binding
from ollama_workstation.agents.mcp_gateway import CompositeToolRegistry, MCPToolGateway
from ollama_workstation.agents.runtime import AgentEvent, AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.model_intelligence import ModelIntelligence
from ollama_workstation.models.routing import (
    ModelRouter,
    RoutingRequest,
    infer_task_capabilities,
)
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.ui.components import (
    AutoTextBrowser,
    Card,
    PageHeader,
    button,
    divider,
    label,
)
from ollama_workstation.ui.formatting import conversation_to_markdown, render_markdown
from ollama_workstation.ui.intelligence_core import IntelligenceCore
from ollama_workstation.ui.pages.common import (
    format_timestamp,
    prepare_list,
    short_path,
    two_line_item,
)
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.theme import COLORS
from ollama_workstation.ui.workers import FunctionWorker

MODES = [
    ("Model chat", "model"),
    ("Agent workspace", "agent"),
    ("Auto route", "routed"),
    ("Workforce", "workforce"),
]
SENSITIVE_NAMES = {".env", ".env.local", "id_rsa", "id_ed25519", "credentials", "secrets"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx"}
TEXT_SUFFIXES = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml",
    ".html", ".css", ".scss", ".sql", ".xml", ".csv", ".log", ".rs", ".go", ".java", ".c",
    ".h", ".cpp", ".hpp", ".ini", ".cfg", ".sh", ".ps1", ".bat",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _open_local(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


@dataclass(slots=True)
class Attachment:
    path: Path
    name: str
    is_image: bool
    content: str = ""
    binary_base64: str = ""
    size_bytes: int = 0


class ChatComposerEdit(QPlainTextEdit):
    submit = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submit.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MessageBubble(QFrame):
    def __init__(
        self,
        role: str,
        content: str,
        *,
        meta: dict[str, Any] | None = None,
        parent: QWidget | None = None,
        on_copy: Any = None,
        on_regenerate: Any = None,
        on_branch: Any = None,
        on_edit: Any = None,
    ) -> None:
        super().__init__(parent)
        self._content = content
        self._role = role
        self.setObjectName("UserMessage" if role == "user" else "AssistantMessage")
        self.setStyleSheet(
            "QFrame#UserMessage{background:rgba(42,107,135,0.16);"
            f"border:1px solid rgba(55,216,244,0.14);border-radius:13px;}}"
            "QFrame#AssistantMessage{background:rgba(12,23,37,0.68);"
            f"border:1px solid {COLORS['border_soft']};border-radius:13px;}}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 9)
        layout.setSpacing(5)
        head = QHBoxLayout()
        head.setSpacing(7)
        avatar = QLabel("YOU" if role == "user" else "11")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(22, 22)
        avatar.setStyleSheet(
            f"background:{'#17485b' if role == 'user' else COLORS['surface_high']};"
            f"color:{COLORS['ice'] if role == 'user' else COLORS['cyan']};"
            "border-radius:11px;font-size:9px;font-weight:700;"
        )
        head.addWidget(avatar)
        head.addWidget(label("You" if role == "user" else "11vated Intelligence", "Body"))
        meta_parts = []
        if meta:
            if meta.get("model"):
                meta_parts.append(str(meta["model"]))
            if meta.get("mode"):
                meta_parts.append(str(meta["mode"]))
        if meta_parts:
            head.addWidget(label(" · ".join(meta_parts), "Faint"))
        head.addStretch(1)
        self.status_label = label("", "Faint")
        head.addWidget(self.status_label)
        layout.addLayout(head)
        self.text = AutoTextBrowser()
        self.text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.text)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        actions.addStretch(1)
        if role == "assistant" and on_copy is not None:
            actions.addWidget(button("Copy", variant="Ghost", on_click=lambda: on_copy(content)))
        if role == "assistant" and on_regenerate is not None:
            actions.addWidget(button("Regenerate", variant="Ghost", on_click=on_regenerate))
        if role == "user" and on_edit is not None:
            actions.addWidget(button("Edit", variant="Ghost", on_click=on_edit))
        if on_branch is not None:
            actions.addWidget(button("Branch", variant="Ghost", on_click=on_branch))
        layout.addLayout(actions)
        self.set_content(content)

    @property
    def content(self) -> str:
        return self._content

    def set_content(self, value: str) -> None:
        self._content = value
        self.text.set_html(render_markdown(value) if value else (
            f"<span style='color:{COLORS['muted']};'>Thinking through the request…</span>"
        ))

    def set_status(self, value: str, tone: str = "muted") -> None:
        self.status_label.setText(value)
        self.status_label.setStyleSheet(f"color:{COLORS.get(tone, COLORS['muted'])};")


class ArtifactPreviewDialog(QDialog):
    """Inspect a local artifact without leaving the conversation."""

    def __init__(self, path: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"Artifact · {path.name}")
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(9)
        layout.addWidget(label("ARTIFACT INSPECTION", "Eyebrow"))
        layout.addWidget(label(path.name, "PageTitle"))
        layout.addWidget(label(str(path), "Faint", wrap=True))
        layout.addWidget(divider())
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        suffix = path.suffix.casefold()
        if suffix in IMAGE_SUFFIXES:
            image = QPixmap(str(path))
            if not image.isNull():
                preview = QLabel()
                preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                preview.setPixmap(image.scaled(
                    760, 430, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                layout.addWidget(preview, 1)
            else:
                layout.addWidget(label("The image could not be decoded.", "Muted"))
        elif suffix in TEXT_SUFFIXES or size < 256_000:
            viewer = QPlainTextEdit()
            viewer.setReadOnly(True)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:500_000]
            except OSError as exc:
                content = f"Could not read artifact: {exc}"
            viewer.setPlainText(content)
            layout.addWidget(viewer, 1)
        else:
            layout.addWidget(label(
                f"Binary or unsupported preview · {size:,} bytes. Open it externally to inspect.",
                "Muted", wrap=True,
            ))
            layout.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addWidget(label(f"{size:,} bytes", "Faint"))
        bottom.addStretch(1)
        bottom.addWidget(button(
            "Open externally", variant="Ghost",
            on_click=lambda: _open_local(path),
        ))
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)


class ChatPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self.conversation_id = ""
        self.messages: list[dict[str, Any]] = []
        self._all_conversations: list[dict[str, Any]] = []
        self._attachments: list[Attachment] = []
        self._bubbles: list[MessageBubble] = []
        self._assistant_bubble: MessageBubble | None = None
        self._assistant_text = ""
        self._active_worker: FunctionWorker | None = None
        self._cancel_event: Event | None = None
        self._activity_id = ""
        self._busy = False
        self._stream_started = False
        self._last_stream_paint = 0.0
        self._artifacts: list[Path] = []
        self._last_route: dict[str, Any] = {}
        self._all_models: list[str] = []
        self._request_model = ""
        self._request_mode = ""
        self._context_auto_hidden = False
        self._context_pinned = False
        self.setAcceptDrops(True)
        self._build()
        self._connect()
        self._new_conversation(force=True)
        self.refresh_models()
        self.refresh_conversations()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 0)
        root.setSpacing(13)
        self.new_button = button("New session", variant="Primary", on_click=self._new_conversation)
        self.export_button = button("Export", variant="Ghost", on_click=self.export_conversation)
        self.context_button = button("Hide context", variant="Ghost", on_click=self._toggle_context)
        root.addWidget(PageHeader(
            "INTELLIGENCE / DIALOGUE",
            "Advanced Chat",
            "Converse with one model, an equipped agent, an automatic route or a coordinated team.",
            actions=[self.new_button, self.export_button, self.context_button],
        ))

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.setChildrenCollapsible(False)
        self._build_conversation_rail(main_split)
        self._build_conversation_center(main_split)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([250, 1000])
        root.addWidget(main_split, 1)

    def _build_conversation_rail(self, split: QSplitter) -> None:
        panel = Card(margins=(12, 13, 12, 12))
        panel.setMinimumWidth(210)
        panel.setMaximumWidth(285)
        panel.body.addWidget(label("SESSIONS", "Eyebrow"))
        panel.body.addWidget(label("Conversations", "SectionTitle"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sessions…")
        self.search.setObjectName("Search")
        self.search.textChanged.connect(self._filter_conversations)
        panel.body.addWidget(self.search)
        self.conversation_list = QListWidget()
        self.conversation_list.currentItemChanged.connect(self._conversation_selected)
        prepare_list(self.conversation_list, row_height=50)
        panel.body.addWidget(self.conversation_list, 1)
        self.delete_button = button(
            "Delete selected session", variant="Ghost", on_click=self._delete_conversation
        )
        panel.body.addWidget(self.delete_button)
        panel.body.addWidget(divider())
        note = label(
            "Sessions persist locally in SQLite. Attachments are held only for the current send.",
            "Faint", wrap=True,
        )
        panel.body.addWidget(note)
        split.addWidget(panel)

    def _build_conversation_center(self, split: QSplitter) -> None:
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)

        controls = QFrame()
        controls.setObjectName("Inset")
        controls.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(12, 8, 12, 8)
        controls_layout.setSpacing(8)
        self.mode_label = label("MODE", "MetricLabel")
        controls_layout.addWidget(self.mode_label)
        self.mode_combo = QComboBox()
        for text, data in MODES:
            self.mode_combo.addItem(text, data)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        controls_layout.addWidget(self.mode_combo)
        self.model_label = label("MODEL", "MetricLabel")
        controls_layout.addWidget(self.model_label)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(0)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model_combo.currentTextChanged.connect(self._model_changed)
        controls_layout.addWidget(self.model_combo, 1)
        self.agent_combo = QComboBox()
        self.agent_combo.setToolTip("Saved agent identities from Workforce Studio.")
        self.agent_combo.currentIndexChanged.connect(self._agent_changed)
        controls_layout.addWidget(self.agent_combo, 1)
        self.agent_combo.hide()
        self.live_status = label("Standing by", "Faint")
        controls_layout.addWidget(self.live_status)
        center_layout.addWidget(controls)

        inner = QSplitter(Qt.Orientation.Horizontal)
        inner.setChildrenCollapsible(False)
        chat = QWidget()
        chat_layout = QVBoxLayout(chat)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(9)
        self.session_title = label("New intelligence session", "SectionTitle")
        chat_layout.addWidget(self.session_title)

        self.messages_scroll = QScrollArea()
        self.messages_scroll.setWidgetResizable(True)
        self.messages_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.messages_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.messages_container = QWidget()
        self.messages_container.setObjectName("PageBody")
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(0, 0, 6, 4)
        self.messages_layout.setSpacing(10)
        self.messages_layout.addStretch(1)
        self.messages_scroll.setWidget(self.messages_container)
        chat_layout.addWidget(self.messages_scroll, 1)
        self.empty = self._welcome_widget()
        self.empty.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.messages_layout.insertWidget(0, self.empty)
        self._update_empty_stretch()

        composer_card = Card(margins=(12, 9, 12, 9), spacing=6)
        self.attachment_row = QHBoxLayout()
        self.attachment_row.setSpacing(6)
        self.attachment_row.addStretch(1)
        composer_card.body.addLayout(self.attachment_row)
        self.composer = ChatComposerEdit()
        self.composer.setObjectName("Composer")
        self.composer.setPlaceholderText(
            "Ask the local intelligence…  Enter sends, Shift+Enter adds a line."
        )
        self.composer.setMaximumHeight(146)
        self.composer.submit.connect(self.send_message)
        composer_card.body.addWidget(self.composer)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.attach_button = button("Attach files", variant="Ghost", on_click=self._choose_files)
        bottom.addWidget(self.attach_button)
        self.mode_hint = label("Direct model conversation", "Faint")
        bottom.addWidget(self.mode_hint)
        bottom.addStretch(1)
        self.cancel_button = button("Stop", variant="Danger", on_click=self.stop_generation)
        self.cancel_button.setEnabled(False)
        bottom.addWidget(self.cancel_button)
        self.send_button = button("Send", variant="Primary", on_click=self.send_message)
        bottom.addWidget(self.send_button)
        composer_card.body.addLayout(bottom)
        chat_layout.addWidget(composer_card)
        inner.addWidget(chat)

        self.context_panel = self._build_context_panel()
        inner.addWidget(self.context_panel)
        inner.setStretchFactor(0, 1)
        inner.setStretchFactor(1, 0)
        inner.setSizes([800, 285])
        center_layout.addWidget(inner, 1)
        split.addWidget(center)

    def _welcome_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(8)
        layout.addStretch(1)
        title = label("A quiet line to local intelligence.", "HeroTitle", wrap=True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        copy = label(
            "One model, one agent, one route or a coordinated workforce. All local.",
            "Muted", wrap=True,
        )
        copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copy.setMaximumWidth(560)
        copy.setMinimumHeight(38)
        layout.addWidget(copy, alignment=Qt.AlignmentFlag.AlignCenter)
        prompt_wrap = QWidget()
        prompt_wrap.setMaximumWidth(500)
        prompts = QGridLayout(prompt_wrap)
        prompts.setContentsMargins(0, 4, 0, 0)
        prompts.setSpacing(7)
        for index, text in enumerate((
            "Explain this architecture",
            "Compare installed models",
            "Plan a research task",
        )):
            prompts.addWidget(
                button(text, variant="Secondary",
                       on_click=partial(self._use_prompt, text)),
                index // 2, index % 2,
            )
        layout.addWidget(prompt_wrap, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)
        return widget

    def _build_context_panel(self) -> QWidget:
        panel = Card(margins=(13, 12, 13, 12), spacing=0)
        panel.setMinimumWidth(250)
        panel.setMaximumWidth(330)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("PageBody")
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 4, 0)
        body.setSpacing(7)
        scroll.setWidget(content)
        panel.body.addWidget(scroll, 1)
        self.context_scroll = scroll

        self.chat_core = IntelligenceCore(compact=True, caption=False)
        self.chat_core.setMinimumHeight(156)
        self.chat_core.setMaximumHeight(190)
        body.addWidget(self.chat_core)
        self.context_state = label("STANDBY", "Eyebrow")
        self.context_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.context_state)
        body.addWidget(divider())
        body.addWidget(label("ACTIVE INTELLIGENCE", "MetricLabel"))
        self.ctx_model = label("No model selected", "Body", wrap=True)
        body.addWidget(self.ctx_model)
        self.ctx_profile = label("No profile", "Faint", wrap=True)
        body.addWidget(self.ctx_profile)
        body.addWidget(divider())
        body.addWidget(label("CONTEXT ESTIMATE", "MetricLabel"))
        self.ctx_context = label("—", "Body")
        body.addWidget(self.ctx_context)
        body.addWidget(label("TOOLS", "MetricLabel"))
        self.ctx_tools = label("Read · list · search · write", "Faint", wrap=True)
        body.addWidget(self.ctx_tools)
        body.addWidget(label("WORKSPACE", "MetricLabel"))
        self.ctx_workspace = label("", "Mono", wrap=True)
        body.addWidget(self.ctx_workspace)
        body.addWidget(divider())
        body.addWidget(label("EXECUTION TRACE", "MetricLabel"))
        self.trace = QListWidget()
        self.trace.setMinimumHeight(82)
        self.trace.setMaximumHeight(160)
        self.trace.setStyleSheet("QListWidget{background:transparent;border:0;}")
        prepare_list(self.trace, spacing=1)
        body.addWidget(self.trace)
        body.addWidget(label("ARTIFACTS", "MetricLabel"))
        self.artifact_list = QListWidget()
        self.artifact_list.setMaximumHeight(76)
        self.artifact_list.itemDoubleClicked.connect(self._open_artifact)
        prepare_list(self.artifact_list, spacing=1)
        body.addWidget(self.artifact_list)
        body.addWidget(divider())
        self.context_note = label("Local-first · private by default", "Faint", wrap=True)
        body.addWidget(self.context_note)
        return panel

    def _connect(self) -> None:
        events = self.context.events
        events.data_changed.connect(self._data_changed)
        events.core_state.connect(self._core_state_changed)
        events.theme_changed.connect(self._apply_motion)
        events.compose_workforce.connect(self._receive_workforce_objective)
        self._apply_motion()
        self._mode_changed()

    def _apply_motion(self) -> None:
        self.chat_core.set_motion(
            reduced=self.context.settings.reduced_motion,
            intensity=self.context.settings.animation_intensity,
        )

    def _core_state_changed(self, state: str, detail: str) -> None:
        self.chat_core.set_state(state, detail)
        self.context_state.setText(state.replace("_", " ").upper())

    def _data_changed(self, kind: str) -> None:
        if kind in {"models", "profiles", "settings", "runs"}:
            self.refresh_models()

    def refresh_models(self) -> None:
        selected = self.model_combo.currentText()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self._all_models = [item.name for item in self.context.models]
        for name in self._all_models:
            self.model_combo.addItem(name)
        if selected and selected in self._all_models:
            self.model_combo.setCurrentText(selected)
        self.model_combo.blockSignals(False)
        self._refresh_agent_combo()
        self._sync_context()

    def _refresh_agent_combo(self) -> None:
        self.agent_combo.blockSignals(True)
        self.agent_combo.clear()
        self.agent_combo.addItem("Agent identity · model profile", "")
        for preset in self.context.settings.agent_presets:
            name = str(preset.get("name") or "").strip()
            model = str(preset.get("model") or "").strip()
            if name:
                self.agent_combo.addItem(f"{name} · {model}", name)
        self.agent_combo.blockSignals(False)

    def _agent_changed(self, _index: int) -> None:
        self._sync_context()

    def _model_changed(self, name: str) -> None:
        if name:
            profile = self.context.profile_for(name)
            if profile is not None and self.mode_combo.currentData() == "agent":
                self.composer.setPlaceholderText(
                    f"Give {profile.professional_title} an objective… Enter sends."
                )
        self._sync_context()

    def _mode_changed(self, *_args: object) -> None:
        mode = str(self.mode_combo.currentData() or "model")
        self.agent_combo.setVisible(mode == "agent")
        hints = {
            "model": "Direct model conversation · no tools",
            "agent": "Bounded agent · workspace tools · host policy",
            "routed": "Automatic route · observed and declared evidence",
            "workforce": "Compose in Workforce Studio · approval required",
        }
        self.mode_hint.setText(hints[mode])
        self.attach_button.setEnabled(mode != "workforce")
        if mode == "workforce":
            self.model_combo.setEnabled(False)
        else:
            self.model_combo.setEnabled(True)
        self._sync_context()

    def _sync_context(self) -> None:
        model = self.model_combo.currentText()
        profile = self.context.profile_for(model) if model else None
        self.ctx_model.setText(model or "No installed model")
        self.model_combo.setToolTip(f"Active model: {model}" if model else "No installed model")
        self.mode_combo.setToolTip(self.mode_hint.text())
        if profile:
            self.ctx_profile.setText(
                f"{profile.professional_title}\n{profile.agentability_status.replace('_', ' ')}"
            )
            context = profile.context_length
        else:
            self.ctx_profile.setText("No saved model profile")
            context = None
        chars = sum(len(str(item.get("content") or "")) for item in self.messages)
        estimate = max(1, chars // 4)
        self.ctx_context.setText(
            f"~{estimate:,} tokens" + (f" / {context:,}" if context else "")
        )
        preset_name = str(self.agent_combo.currentData() or "")
        preset = next(
            (item for item in self.context.settings.agent_presets
             if str(item.get("name") or "") == preset_name),
            None,
        )
        if preset:
            self.ctx_profile.setText(
                f"Agent preset · {preset.get('role') or 'specialist'}\n"
                f"{preset.get('instructions') or 'No custom operating note'}"
            )
        workspace = self.context.workspace_path()
        self.ctx_workspace.setText(short_path(workspace))
        self.ctx_workspace.setToolTip(str(workspace))
        mode = str(self.mode_combo.currentData() or "model")
        if mode == "model":
            self.ctx_tools.setText("No tools · direct inference")
        elif mode == "agent":
            self.ctx_tools.setText("Read · list · search · write\nShell disabled")
        elif mode == "routed":
            self.ctx_tools.setText("No tools · host-selected model")
        else:
            self.ctx_tools.setText("Workforce Studio policy\nApproval required")
        self.ctx_context.setToolTip("Estimated from conversation characters; not exact tokenization.")

    def _toggle_context(self) -> None:
        visible = not self.context_panel.isVisible()
        self.context_panel.setVisible(visible)
        self._context_pinned = visible
        if visible:
            self._context_auto_hidden = False
        self.context_button.setText("Hide context" if visible else "Show context")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_responsive_state)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_responsive_state()

    def _apply_responsive_state(self) -> None:
        if not hasattr(self, "context_panel"):
            return
        narrow = self.width() < 1030
        self.mode_label.setVisible(not narrow)
        self.model_label.setVisible(not narrow)
        self.live_status.setVisible(not narrow)
        if narrow and not self._context_auto_hidden and not self._context_pinned:
            # Explicit hide is required here: isVisible() is false while the
            # page itself is hidden, and visibility would otherwise return
            # when the stack makes this page current.
            self.context_panel.hide()
            self._context_auto_hidden = True
            self.context_button.setText("Show context")
        elif not narrow and self._context_auto_hidden and not self._context_pinned:
            self.context_panel.show()
            self._context_auto_hidden = False
            self.context_button.setText("Hide context")

    def _use_prompt(self, value: str) -> None:
        self.composer.setPlainText(value)
        self.composer.setFocus()

    def _new_conversation(self, force: bool = False) -> None:
        if self._busy and not force:
            self.context.notify("Stop the active response before starting another session.")
            return
        self.conversation_id = ""
        self.messages = []
        self._artifacts = []
        self._request_model = ""
        self._request_mode = ""
        self._clear_messages()
        self.empty.show()
        self._update_empty_stretch()
        self.session_title.setText("New intelligence session")
        self._sync_context()
        self.conversation_list.clearSelection()
        self.refresh_conversations()

    def _clear_messages(self) -> None:
        for bubble in self._bubbles:
            bubble.deleteLater()
        self._bubbles = []
        self._assistant_bubble = None
        self.artifact_list.clear()

    def refresh_conversations(self) -> None:
        try:
            self._all_conversations = self.context.repository.list_conversations(100)
        except Exception:
            self._all_conversations = []
        self._filter_conversations()

    def _filter_conversations(self, *_args: object) -> None:
        query = self.search.text().strip().casefold() if hasattr(self, "search") else ""
        current = self.conversation_id
        self.conversation_list.blockSignals(True)
        self.conversation_list.clear()
        for conversation in self._all_conversations:
            haystack = (
                f"{conversation.get('title')} {conversation.get('preview')} "
                f"{conversation.get('model_name')}"
            ).casefold()
            if query and query not in haystack:
                continue
            item = two_line_item(
                f"{conversation.get('title') or 'Untitled session'}\n"
                f"{format_timestamp(conversation.get('updated_at'), short=True)} · "
                f"{conversation.get('message_count', 0)} messages",
                data=conversation.get("conversation_id"),
            )
            self.conversation_list.addItem(item)
            if conversation.get("conversation_id") == current:
                self.conversation_list.setCurrentItem(item)
        self.conversation_list.blockSignals(False)

    def _conversation_selected(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is None or self._busy:
            return
        conversation_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if not conversation_id or conversation_id == self.conversation_id:
            return
        record = self.context.repository.get_conversation(conversation_id)
        if record is None:
            return
        self.conversation_id = conversation_id
        self.messages = [
            item for item in (record.get("messages") or []) if isinstance(item, dict)
        ]
        self._artifacts = [
            Path(str(item["artifact"])) for item in self.messages
            if isinstance(item.get("artifact"), str)
        ]
        self._render_messages()
        self.session_title.setText(str(record.get("title") or "Session"))
        self._sync_context()
        self._render_artifacts()

    def _render_messages(self) -> None:
        self._clear_messages()
        self.empty.setVisible(not self.messages)
        self._update_empty_stretch()
        for index, message in enumerate(self.messages):
            role = str(message.get("role") or "assistant")
            is_user = role == "user"
            meta = (
                {"model": message.get("model"), "mode": message.get("mode")}
                if role == "assistant" else {}
            )
            bubble = self._add_bubble(
                role, str(message.get("content") or ""), meta=meta,
                regenerate=self._is_last_assistant(message) and not self._busy,
                on_branch=(
                    lambda checked=False, position=index: self._branch_from(position)
                    if not self._busy else None
                ),
                on_edit=(
                    lambda checked=False, position=index, can_edit=is_user: self._edit_message(position)
                    if not self._busy and can_edit else None
                ),
            )
            if role == "assistant" and message.get("error"):
                bubble.set_status("FAILED", "red")
        self._scroll_to_bottom()

    def _is_last_assistant(self, message: dict[str, Any]) -> bool:
        if not self.messages:
            return False
        return bool(message is self.messages[-1] and message.get("role") == "assistant")

    def _add_bubble(
        self,
        role: str,
        content: str,
        *,
        meta: dict[str, Any] | None = None,
        regenerate: bool = False,
        on_branch: Any = None,
        on_edit: Any = None,
    ) -> MessageBubble:
        bubble = MessageBubble(
            role, content, meta=meta,
            on_copy=self._copy_text,
            on_regenerate=self.regenerate if regenerate else None,
            on_branch=on_branch,
            on_edit=on_edit,
        )
        self._bubbles.append(bubble)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        self._update_empty_stretch()
        return bubble

    def _copy_text(self, value: str) -> None:
        QApplication.clipboard().setText(value)
        self.context.notify("Message copied to clipboard.")

    def _update_empty_stretch(self) -> None:
        """Let the welcome block occupy the quiet viewport, but keep messages top-aligned."""
        count = self.messages_layout.count()
        if count < 2:
            return
        showing_welcome = not self.empty.isHidden()
        self.messages_layout.setStretch(0, 1 if showing_welcome else 0)
        self.messages_layout.setStretch(count - 1, 0 if showing_welcome else 1)

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(30, lambda: self.messages_scroll.verticalScrollBar().setValue(
            self.messages_scroll.verticalScrollBar().maximum()
        ))

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach files to this message",
            str(Path.home()),
            "Documents and images (*.txt *.md *.py *.js *.ts *.json *.toml *.yaml *.yml "
            "*.html *.css *.sql *.csv *.log *.rs *.go *.java *.png *.jpg *.jpeg *.webp)",
        )
        if paths:
            self._add_attachments([Path(item) for item in paths])

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [
            Path(url.toLocalFile()) for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self._add_attachments(paths)
            event.acceptProposedAction()

    def _add_attachments(self, paths: list[Path]) -> None:
        for path in paths:
            if any(item.path == path for item in self._attachments):
                continue
            if len(self._attachments) >= 8:
                self.context.notify("At most eight attachments can be sent at once.")
                break
            suffix = path.suffix.casefold()
            if path.name.casefold() in SENSITIVE_NAMES or suffix in SENSITIVE_SUFFIXES:
                self.context.notify(f"Refused sensitive-looking attachment: {path.name}")
                continue
            try:
                size = path.stat().st_size
            except OSError as exc:
                self.context.notify(f"Could not read {path.name}: {exc}")
                continue
            if size > 8 * 1024 * 1024:
                self.context.notify(f"Skipped {path.name}: larger than 8 MB.")
                continue
            is_image = suffix in IMAGE_SUFFIXES or str(
                mimetypes.guess_type(path.name)[0] or ""
            ).startswith("image/")
            try:
                raw = path.read_bytes()
            except OSError as exc:
                self.context.notify(f"Could not read {path.name}: {exc}")
                continue
            content = ""
            binary = ""
            if is_image:
                binary = base64.b64encode(raw).decode("ascii")
            else:
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self.context.notify(f"Skipped {path.name}: not UTF-8 text.")
                    continue
                if len(content) > 200_000:
                    content = content[:200_000] + "\n[attachment truncated]"
                content, redacted = WorkspaceTools.redact_sensitive_text(content)
                if redacted:
                    self.context.notify(
                        f"Attachment {path.name}: redacted {redacted} credential-like value(s)."
                    )
            self._attachments.append(
                Attachment(path, path.name, is_image, content, binary, size)
            )
        self._render_attachments()

    def _render_attachments(self) -> None:
        while self.attachment_row.count() > 1:
            item = self.attachment_row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for attachment in self._attachments:
            chip = QFrame()
            chip.setObjectName("Inset")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(8, 3, 4, 3)
            chip_layout.setSpacing(5)
            icon = "▧" if attachment.is_image else "⌑"
            chip_layout.addWidget(label(f"{icon} {attachment.name}", "Faint"))
            remove = button("×", variant="Ghost",
                            on_click=partial(self._remove_attachment, attachment))
            remove.setFixedSize(20, 20)
            chip_layout.addWidget(remove)
            self.attachment_row.insertWidget(self.attachment_row.count() - 1, chip)

    def _remove_attachment(self, attachment: Attachment) -> None:
        self._attachments = [item for item in self._attachments if item is not attachment]
        self._render_attachments()

    def _compose_attachment_text(self) -> str:
        if not self._attachments:
            return ""
        sections = []
        for item in self._attachments:
            if item.is_image:
                sections.append(f"[Image attached: {item.name}]")
            else:
                sections.append(f"[Attached file: {item.name}]\n{item.content}")
        return "\n\n".join(sections)

    def send_message(self) -> None:
        if self._busy:
            return
        text = self.composer.toPlainText().strip()
        if not text:
            self.context.notify("Write a message before sending.")
            return
        self._send(text, record_user=True)

    def _send(self, text: str, *, record_user: bool) -> None:
        mode = str(self.mode_combo.currentData() or "model")
        model = self.model_combo.currentText()
        if mode in {"model", "agent", "routed"} and not model:
            self.context.notify("No installed model is available. Start Ollama and refresh.")
            return
        profile = self.context.profile_for(model) if model else None
        if mode == "agent" and profile is None:
            self.context.notify("Agent mode requires a saved profile for the selected model.")
            return
        self._request_model = model
        self._request_mode = mode
        if not self.conversation_id:
            self.conversation_id = str(uuid.uuid4())
            self.session_title.setText(text[:72])
        self.empty.hide()
        attachment_text = self._compose_attachment_text()
        user_content = text + (f"\n\n{attachment_text}" if attachment_text else "")
        if record_user:
            self.messages.append({
                "role": "user", "content": user_content, "created_at": datetime.now(UTC).isoformat(),
                "mode": mode, "model": model,
            })
            self._add_bubble("user", user_content)
        self._save_conversation()
        self.composer.clear()
        attachments = list(self._attachments)
        self._attachments = []
        self._render_attachments()
        self._assistant_text = ""
        self._assistant_bubble = self._add_bubble("assistant", "", meta={"model": model, "mode": mode})
        self._assistant_bubble.set_status("WORKING", "cyan")
        self._set_busy(True)
        self.trace.clear()
        self.live_status.setText("Preparing request…")
        if mode == "routed":
            self.context.set_state("routing", "Ranking installed local models")
        elif mode == "agent":
            self.context.set_state("planning", "Preparing bounded agent context")
        elif mode == "workforce":
            self.context.set_state("awaiting_approval", "Workforce approval required")
        else:
            self.context.set_state("thinking", f"Preparing {mode} request")
        if mode == "workforce":
            self.messages.append({
                "role": "assistant",
                "content": (
                    "This objective has been handed to Workforce Studio. Review the host-validated "
                    "plan, assignments and approval boundary before anything executes."
                ),
                "created_at": datetime.now(UTC).isoformat(),
                "model": model, "mode": mode,
            })
            self._assistant_bubble.set_content(self.messages[-1]["content"])
            self._assistant_bubble.set_status("AWAITING REVIEW", "amber")
            self._save_conversation()
            self._finish_request()
            self.context.events.compose_workforce.emit(text)
            self.context.navigate("workforce")
            return
        if mode == "agent" and profile is not None:
            self._start_agent(text, model, profile, attachments)
        elif mode == "routed":
            self._start_routed(user_content, model, attachments)
        else:
            self._start_model_chat(user_content, model, attachments)

    def _history_messages(self, *, include_current: bool = True) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        raw = self.messages[:-1] if not include_current else self.messages
        for item in raw:
            if item.get("role") not in {"user", "assistant"}:
                continue
            content = str(item.get("content") or "")
            if item.get("error"):
                continue
            if content:
                messages.append({"role": item["role"], "content": content})
        system = (
            "You are the local intelligence interface of 11vatedTech. Answer directly and "
            "accurately. Use the provided context. Never claim to have used tools when you "
            "did not. Be concise unless the task needs depth."
        )
        if self.mode_combo.currentData() == "model":
            return [{"role": "system", "content": system}, *messages[-24:]]
        return messages[-24:]

    def _start_model_chat(
        self, user_content: str, model: str, attachments: list[Attachment]
    ) -> None:
        messages = self._history_messages()
        images = [item.binary_base64 for item in attachments if item.is_image]
        cancel = Event()
        self._cancel_event = cancel
        worker: FunctionWorker
        worker = FunctionWorker(
            self._stream_chat, model, messages, images, cancel,
            lambda content, thinking: worker.signals.stream.emit(content, thinking),
        )
        self._active_worker = worker
        worker.signals.stream.connect(self._on_stream)
        worker.signals.result.connect(self._chat_ready)
        worker.signals.error.connect(self._chat_failed)
        worker.signals.finished.connect(self._worker_finished)
        self._activity_id = self.context.activity.begin(
            "MODEL", model, "Streaming local completion", cancel_event=cancel,
            cancel=lambda: cancel.set(), page="chat", metadata={"mode": "model"},
        )
        self.context.pool.start(worker)

    def _stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        images: list[str],
        cancel: Event,
        emit: Any,
    ) -> dict[str, Any]:
        if images:
            metadata = self.context.client.show_model(model)
            capabilities = {
                str(item).casefold()
                for item in (metadata.get("capabilities") or [])
                if isinstance(item, str)
            }
            if "vision" not in capabilities:
                raise ValueError(
                    "The selected model does not declare vision capability; refusing image input."
                )
            messages = [dict(item) for item in messages]
            for item in reversed(messages):
                if item.get("role") == "user":
                    item["images"] = images
                    break
        return self.context.client.chat(
            model,
            messages,
            keep_alive=self.context.settings.keep_alive,
            cancel_event=cancel,
            stream_callback=emit,
        )

    def _start_routed(
        self, user_content: str, preferred: str, attachments: list[Attachment]
    ) -> None:
        cancel = Event()
        self._cancel_event = cancel
        messages = self._history_messages()
        images = [item.binary_base64 for item in attachments if item.is_image]
        worker: FunctionWorker
        worker = FunctionWorker(
            self._routed_task, messages, images, preferred, cancel,
            lambda content, thinking: worker.signals.stream.emit(content, thinking),
        )
        self._active_worker = worker
        worker.signals.progress.connect(self._on_progress)
        worker.signals.stream.connect(self._on_stream)
        worker.signals.result.connect(self._routed_ready)
        worker.signals.error.connect(self._chat_failed)
        worker.signals.finished.connect(self._worker_finished)
        self._activity_id = self.context.activity.begin(
            "ROUTE", "Automatic model selection", "Ranking installed intelligence",
            cancel_event=cancel, cancel=lambda: cancel.set(), page="chat",
            metadata={"mode": "routed"},
        )
        self.context.pool.start(worker)

    def _routed_task(
        self,
        messages: list[dict[str, Any]],
        images: list[str],
        preferred: str,
        cancel: Event,
        emit: Any,
    ) -> dict[str, Any]:
        intelligence: dict[str, ModelIntelligence] = {}
        for profile in self.context.profiles:
            if profile.intelligence:
                try:
                    intelligence[profile.model_name] = ModelIntelligence.from_dict(profile.intelligence)
                except (TypeError, ValueError):
                    pass
        last_user = next(
            (str(item.get("content") or "") for item in reversed(messages)
             if item.get("role") == "user"),
            "",
        )
        context_tokens = max(1, sum(len(str(item.get("content") or "")) for item in messages) // 4)
        request = RoutingRequest(
            preferred_capabilities=infer_task_capabilities(last_user),
            context_tokens=context_tokens,
            preferred_models=(preferred,) if preferred else (),
        )
        decision = ModelRouter().rank(
            self.context.models,
            request=request,
            profiles=self.context.profiles,
            intelligence=intelligence,
            host=self.context.telemetry,
        )
        if decision.selected is None:
            details = "; ".join(
                f"{item.model_name}: {item.reason}" for item in decision.candidates[:4]
            )
            raise ValueError(f"No installed model is eligible for this route. {details}")
        chosen = decision.selected
        self.context.events.status.emit(
            f"Routed to {chosen.model_name} ({chosen.score:.1f})"
        )
        if images:
            metadata = self.context.client.show_model(chosen.model_name)
            caps = {str(item).casefold() for item in (metadata.get("capabilities") or [])}
            if "vision" not in caps:
                raise ValueError(
                    f"Routed model {chosen.model_name} does not declare vision capability."
                )
            for item in reversed(messages):
                if item.get("role") == "user":
                    item["images"] = images
                    break
        response = self.context.client.chat(
            chosen.model_name,
            messages,
            keep_alive=self.context.settings.keep_alive,
            cancel_event=cancel,
            stream_callback=emit,
        )
        return {"response": response, "route": decision.to_dict(), "model": chosen.model_name}

    def _start_agent(
        self, prompt: str, model: str, profile: ModelProfile, attachments: list[Attachment]
    ) -> None:
        if attachments:
            prompt += "\n\n" + self._compose_attachment_text()
        preset_name = str(self.agent_combo.currentData() or "")
        preset = next(
            (item for item in self.context.settings.agent_presets
             if str(item.get("name") or "") == preset_name),
            None,
        )
        instructions = str((preset or {}).get("instructions") or "").strip()
        if instructions:
            prompt = f"Operating instructions:\n{instructions}\n\nObjective:\n{prompt}"
        workspace = self.context.workspace_path()
        cancel = Event()
        self._cancel_event = cancel
        worker: FunctionWorker
        worker = FunctionWorker(
            self._agent_task, model, profile, prompt, workspace, cancel,
            lambda event: worker.signals.progress.emit(f"{event.kind}\t{event.message}"),
        )
        self._active_worker = worker
        worker.signals.progress.connect(self._on_agent_event)
        worker.signals.result.connect(self._agent_ready)
        worker.signals.error.connect(self._chat_failed)
        worker.signals.finished.connect(self._worker_finished)
        self._activity_id = self.context.activity.begin(
            "AGENT", profile.professional_title, f"Workspace: {workspace}",
            cancel_event=cancel, cancel=lambda: cancel.set(), page="chat",
            metadata={"mode": "agent", "workspace": str(workspace)},
        )
        self.ctx_workspace.setText(str(workspace))
        self.context.pool.start(worker)

    def _agent_task(
        self,
        model: str,
        profile: ModelProfile,
        prompt: str,
        workspace: Path,
        cancel: Event,
        progress: Any,
    ) -> Any:
        installed = next(
            (item for item in self.context.profiler.inventory() if item.name == model), None
        )
        if installed is None:
            raise ValueError(f"Model {model!r} is not installed in Ollama.")
        validate_installed_model_binding(profile, installed)
        tools = WorkspaceTools(workspace, allow_shell=False, cancel_event=cancel)
        gateway_context = MCPToolGateway(
            self.context.settings.mcp_servers, cancel_event=cancel
        )
        with gateway_context as gateway:
            composite = CompositeToolRegistry(tools, gateway)
            runtime = AgentRuntime(
                self.context.client,
                max_steps=self.context.settings.max_agent_steps,
                keep_alive=self.context.settings.keep_alive,
                repository=self.context.repository,
                memory_store=self.context.memory_store,
            )
            if gateway.routes:
                progress(AgentEvent("mcp_ready", f"{len(gateway.routes)} approved tool(s) active."))
            # Qualification + RAM/VRAM admission; raises with every blocking reason.
            clearance, policy = clear_repository_run(
                self.context.client, self.context.repository, model_name=model,
                model_digest=profile.digest, tool_definitions=composite.definitions(),
                allow_unqualified=self.context.settings.allow_unqualified_models,
            )
            progress(AgentEvent("execution_cleared", clearance.summary(), clearance.to_dict()))
            return runtime.run(
                model, profile, prompt, composite,
                event=progress, cancel_event=cancel, policy=policy,
            )

    def _on_agent_event(self, payload: str) -> None:
        kind, _, message = payload.partition("\t")
        if not message:
            message = kind
        self._append_trace(kind, message)
        self.live_status.setText(message[:90])
        if kind in {"tool_started", "tool_completed"}:
            self.context.set_state("tool_execution", message)
        elif kind == "thinking":
            self.context.set_state("thinking", message)
        elif kind == "memory_retrieved":
            self.context.set_state("planning", message)
        elif kind in {"verification_started", "verification_recorded"}:
            self.context.set_state("verifying", message)
        if self._assistant_bubble is not None:
            self._assistant_bubble.set_status(kind.replace("_", " ").upper(), "cyan")
        self.context.activity.update(self._activity_id, message)

    def _agent_ready(self, result: Any) -> None:
        content = str(getattr(result, "final_message", "") or "")
        status = str(getattr(result, "status", "unknown"))
        if self._assistant_bubble is not None:
            self._assistant_bubble.set_content(content or "The agent returned no final message.")
            self._assistant_bubble.set_status(status.upper(), "green" if status == "completed" else "amber")
        self.messages.append({
            "role": "assistant", "content": content, "created_at": datetime.now(UTC).isoformat(),
            "model": self._request_model or self.model_combo.currentText(), "mode": "agent",
            "status": status, "error": status != "completed",
        })
        for item in getattr(result, "trace", []) or []:
            data = getattr(item, "data", {}) or {}
            result_data = data.get("result") if isinstance(data, dict) else None
            if isinstance(result_data, dict):
                candidate = result_data.get("path") or result_data.get("relative_path")
                if candidate:
                    path = Path(str(candidate))
                    if path.exists():
                        self._artifacts.append(path)
                        self.messages[-1].setdefault("artifacts", []).append(str(path))
        self._render_artifacts()
        self._save_conversation()
        self.context.set_state("complete" if status == "completed" else "warning",
                               f"Agent finished: {status}")
        self._restore_standby_later()

    def _on_progress(self, message: str) -> None:
        self.live_status.setText(message[:120])
        if self._request_mode == "routed":
            self.context.set_state("routing", message)
        self.context.activity.update(self._activity_id, message)

    def _on_stream(self, content: str, thinking: str) -> None:
        if not self._stream_started:
            self._stream_started = True
            self.context.set_state("generating", "Receiving local model output")
            if self._assistant_bubble is not None:
                self._assistant_bubble.set_status("STREAMING", "cyan")
        self._assistant_text += content
        if thinking and not getattr(self, "_thinking_traced", False):
            self._thinking_traced = True
            self._append_trace("thinking", "Model is emitting internal reasoning", update=False)
        now = time.monotonic()
        if now - self._last_stream_paint > 0.045:
            self._last_stream_paint = now
            if self._assistant_bubble is not None:
                self._assistant_bubble.set_content(self._assistant_text + "▍")
            self._scroll_to_bottom()

    def _load_route_trace(self, route: dict[str, Any]) -> None:
        self._last_route = route
        self.trace.clear()
        selected = route.get("selected") or {}
        for candidate in route.get("candidates", [])[:8]:
            if not isinstance(candidate, dict):
                continue
            marker = "SELECTED" if candidate.get("model_name") == selected.get("model_name") else (
                "ELIGIBLE" if candidate.get("eligible") else "SKIPPED"
            )
            text = (
                f"{marker}  {candidate.get('model_name')}  ·  "
                f"score {float(candidate.get('score') or 0):.1f}\n"
                f"{candidate.get('reason') or ''}"
            )
            self.trace.addItem(text)
        if selected:
            self.live_status.setText(f"Routed to {selected.get('model_name')}")

    def _chat_ready(self, result: dict[str, Any]) -> None:
        message = result.get("message") if isinstance(result, dict) else {}
        content = str((message or {}).get("content") or "")
        if not content:
            self._chat_failed("Ollama returned an empty final message.")
            return
        if self._assistant_bubble is not None:
            self._assistant_bubble.set_content(content)
            speed = self._tokens_per_second(result)
            self._assistant_bubble.set_status(
                f"{speed:.1f} tok/s" if speed else "COMPLETE", "green"
            )
        self.messages.append({
            "role": "assistant", "content": content,
            "created_at": datetime.now(UTC).isoformat(),
            "model": self._request_model or self.model_combo.currentText(), "mode": "model",
            "eval_count": result.get("eval_count"),
        })
        self._save_conversation()
        self.context.set_state("complete", "Local response complete")
        self.live_status.setText("Response complete")
        self._restore_standby_later()

    def _routed_ready(self, result: dict[str, Any]) -> None:
        self._load_route_trace(result.get("route") or {})
        chosen = str(result.get("model") or "")
        if chosen and chosen != self.model_combo.currentText():
            self.ctx_model.setText(chosen)
        response = result.get("response") or {}
        message = response.get("message") or {}
        content = str(message.get("content") or "") if isinstance(message, dict) else ""
        if not content:
            self._chat_failed("Routed model returned an empty final message.")
            return
        if self._assistant_bubble is not None:
            self._assistant_bubble.set_content(content)
            speed = self._tokens_per_second(response)
            self._assistant_bubble.set_status(
                f"ROUTED · {chosen}" + (f" · {speed:.1f} tok/s" if speed else ""), "green"
            )
        self.messages.append({
            "role": "assistant", "content": content,
            "created_at": datetime.now(UTC).isoformat(),
            "model": chosen, "mode": "routed",
            "route": result.get("route"),
        })
        self._save_conversation()
        self.context.set_state("complete", f"Routed completion from {chosen}")
        self._restore_standby_later()

    @staticmethod
    def _tokens_per_second(payload: dict[str, Any]) -> float | None:
        try:
            count = float(payload.get("eval_count") or 0)
            duration = float(payload.get("eval_duration") or 0)
        except (TypeError, ValueError):
            return None
        if count <= 0 or duration <= 0:
            return None
        return count / (duration / 1_000_000_000)

    def _chat_failed(self, message: str) -> None:
        safe = message.splitlines()[0][:500]
        cancelled = self._cancel_event is not None and self._cancel_event.is_set()
        if self._assistant_bubble is not None:
            if cancelled:
                self._assistant_bubble.set_content(
                    self._assistant_text + "\n\n*Generation stopped by the user.*"
                    if self._assistant_text else "*Generation stopped by the user.*"
                )
                self._assistant_bubble.set_status("STOPPED", "amber")
            else:
                self._assistant_bubble.set_content(
                    (self._assistant_text + "\n\n" if self._assistant_text else "")
                    + f"**Execution failed.**\n\n`{safe}`"
                )
                self._assistant_bubble.set_status("FAILED", "red")
                QMessageBox.critical(self, "Intelligence request failed", message[:2400])
        self.messages.append({
            "role": "assistant",
            "content": self._assistant_text if cancelled else f"Execution failed: {safe}",
            "created_at": datetime.now(UTC).isoformat(),
            "model": self._request_model or self.model_combo.currentText(),
            "mode": self._request_mode or self.mode_combo.currentData(),
            "error": not cancelled,
        })
        self._save_conversation()
        self.context.set_state("standby" if cancelled else "error",
                               "Generation stopped" if cancelled else "Request failed")
        if not cancelled:
            self.live_status.setText("Execution failed")

    def _worker_finished(self) -> None:
        if self._activity_id:
            self.context.activity.finish(self._activity_id)
            self._activity_id = ""
        self._assistant_bubble = None
        self._active_worker = None
        self._cancel_event = None
        self._finish_request()

    def _finish_request(self) -> None:
        self._set_busy(False)
        self._stream_started = False
        self._sync_context()
        self.refresh_conversations()
        self._scroll_to_bottom()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.mode_combo.setEnabled(not busy)
        self.model_combo.setEnabled(not busy)
        self.agent_combo.setEnabled(not busy)
        self.attach_button.setEnabled(not busy and self.mode_combo.currentData() != "workforce")
        if not busy:
            self._mode_changed()

    def stop_generation(self) -> None:
        if self._cancel_event is None:
            return
        self._cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.live_status.setText("Stop requested; waiting for the next safe boundary")
        self.context.notify("Stop requested. The active provider or tool request may finish first.")
        if self._activity_id:
            self.context.activity.cancel(self._activity_id)

    def regenerate(self) -> None:
        if self._busy or len(self.messages) < 2:
            return
        last_user = next(
            (item for item in reversed(self.messages) if item.get("role") == "user"), None
        )
        if last_user is None:
            return
        while self.messages and self.messages[-1].get("role") == "assistant":
            self.messages.pop()
        if self._bubbles:
            bubble = self._bubbles.pop()
            bubble.deleteLater()
        self._sync_context()
        self._send(str(last_user.get("content") or ""), record_user=False)

    def _save_conversation(self) -> None:
        if not self.conversation_id:
            return
        title = "Untitled session"
        for item in self.messages:
            if item.get("role") == "user":
                title = " ".join(str(item.get("content") or "").split())[:72] or title
                break
        model = self._request_model or self.model_combo.currentText() or "local"
        try:
            self.context.repository.save_conversation(
                self.conversation_id,
                model,
                title,
                datetime.now(UTC).isoformat(),
                self.messages,
            )
        except Exception as exc:
            self.context.notify(f"Conversation could not be saved: {exc}")
            return
        self.session_title.setText(title)
        self.refresh_conversations()

    def _delete_conversation(self) -> None:
        item = self.conversation_list.currentItem()
        if item is None:
            return
        conversation_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not conversation_id:
            return
        confirmed = QMessageBox.question(
            self, "Delete conversation",
            "Delete this local conversation and its saved messages?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.context.repository.delete_conversation(conversation_id)
        if conversation_id == self.conversation_id:
            self._new_conversation(force=True)
        else:
            self.refresh_conversations()

    def _receive_workforce_objective(self, objective: str) -> None:
        if not objective.strip():
            return
        self.composer.setPlainText(objective)

    def _append_trace(self, kind: str, message: str, *, update: bool = True) -> None:
        text = f"{kind.replace('_', ' ').upper()}  ·  {message}"
        item = two_line_item(text, height=34)
        item.setToolTip(text)
        self.trace.addItem(item)
        self.trace.scrollToBottom()
        if update:
            self.context.activity.update(self._activity_id, message)

    def _render_artifacts(self) -> None:
        self.artifact_list.clear()
        unique = []
        for path in self._artifacts:
            if path not in unique:
                unique.append(path)
        for path in unique[-12:]:
            item = QListWidgetItem(path.name)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.artifact_list.addItem(item)
        if not unique:
            self.artifact_list.addItem("No artifacts in this session.")

    def _open_artifact(self, item: QListWidgetItem) -> None:
        value = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if value:
            path = Path(value)
            if path.is_file():
                ArtifactPreviewDialog(path, self).exec()
            else:
                self.context.notify("That artifact is no longer present on disk.")

    def _branch_from(self, index: int) -> None:
        """Create a new conversation containing the selected message prefix."""
        if self._busy or index < 0 or index >= len(self.messages):
            return
        prefix = [dict(item) for item in self.messages[: index + 1]]
        if not prefix:
            return
        title = "Branch · " + next(
            (" ".join(str(item.get("content") or "").split())[:56]
             for item in prefix if item.get("role") == "user"),
            "untitled",
        )
        self.conversation_id = str(uuid.uuid4())
        self.messages = prefix
        self._artifacts = []
        for item in self.messages:
            artifacts = item.get("artifacts")
            if isinstance(artifacts, list):
                self._artifacts.extend(Path(str(path)) for path in artifacts)
        self._save_conversation()
        self.session_title.setText(title)
        self._render_messages()
        self._render_artifacts()
        self._sync_context()
        self.context.notify(f"Branched {len(prefix)} messages into a new local session.")

    def _edit_message(self, index: int) -> None:
        """Fork before the selected user message and place it back in the composer."""
        if self._busy or index < 0 or index >= len(self.messages):
            return
        target = self.messages[index]
        if target.get("role") != "user":
            return
        prefix = [dict(item) for item in self.messages[:index]]
        self.conversation_id = str(uuid.uuid4())
        self.messages = prefix
        self._artifacts = []
        for item in prefix:
            artifacts = item.get("artifacts")
            if isinstance(artifacts, list):
                self._artifacts.extend(Path(str(path)) for path in artifacts)
        self._save_conversation()
        self._render_messages()
        self._render_artifacts()
        self._sync_context()
        self.composer.setPlainText(str(target.get("content") or ""))
        self.composer.setFocus()
        self.context.notify(
            "Created a branch before the selected message. Edit the text, then send."
        )

    def export_conversation(self) -> None:
        if not self.messages:
            self.context.notify("There is no conversation to export yet.")
            return
        title = self.session_title.text() or "intelligence-session"
        safe = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in title.casefold()
        ).strip("-")[:60] or "intelligence-session"
        destination, _filter = QFileDialog.getSaveFileName(
            self, "Export conversation", str(Path.home() / f"{safe}.md"),
            "Markdown (*.md);;Text (*.txt)",
        )
        if not destination:
            return
        path = Path(destination)
        try:
            path.write_text(
                conversation_to_markdown(
                    title, self.messages,
                    model=self._request_model or self.model_combo.currentText(),
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.context.notify(f"Conversation exported to {path.name}.")

    def _restore_standby_later(self) -> None:
        QTimer.singleShot(
            2800,
            lambda: self.context.set_state("standby", "Local intelligence ready"),
        )

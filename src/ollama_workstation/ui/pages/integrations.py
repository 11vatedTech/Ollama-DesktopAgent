"""Integration Hub: local runtime and explicitly approved MCP connections."""

from __future__ import annotations

import importlib.util
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.mcp_gateway import MCPServerSpec, MCPToolGateway
from ollama_workstation.ui.components import (
    Card,
    PageHeader,
    Pill,
    SectionHeader,
    StatusDot,
    button,
    divider,
    label,
)
from ollama_workstation.ui.state import AppContext
from ollama_workstation.ui.theme import COLORS
from ollama_workstation.ui.workers import FunctionWorker


class IntegrationHubPage(QWidget):
    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.context = context
        self._server_cards: dict[str, dict[str, Any]] = {}
        self._build()
        self._connect()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 0)
        root.setSpacing(14)
        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(7)
        action_layout.addWidget(button("Refresh health", on_click=self.refresh))
        action_layout.addWidget(button("Configure", variant="Secondary",
                                       on_click=lambda: self.context.navigate("settings")))
        root.addWidget(PageHeader(
            "SYSTEM / CONNECTIONS",
            "Integration Hub",
            "See exactly what can reach local intelligence, files, tools or remote services.",
            actions=[actions],
        ))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("PageBody")
        self.layout_body = QVBoxLayout(content)
        self.layout_body.setContentsMargins(0, 0, 6, 26)
        self.layout_body.setSpacing(14)
        self.layout_body.addWidget(self._runtime_card())
        self.layout_body.addWidget(self._servers_header())
        self.servers_card = Card(margins=(17, 15, 17, 14))
        self.server_layout = QVBoxLayout()
        self.server_layout.setSpacing(8)
        self.servers_card.body.addLayout(self.server_layout)
        self.layout_body.addWidget(self.servers_card)
        self.layout_body.addWidget(self._trust_card())
        self.layout_body.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    def _runtime_card(self) -> QWidget:
        card = Card(margins=(18, 16, 18, 15))
        header = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(2)
        copy.addWidget(label("PRIMARY PROVIDER", "Eyebrow"))
        copy.addWidget(label("Local Ollama runtime", "SectionTitle"))
        copy.addWidget(label(
            "The sole inference floor for this workstation. No cloud credential is used.",
            "Muted", wrap=True,
        ))
        header.addLayout(copy, 1)
        self.runtime_pill = Pill("CHECKING", "muted")
        header.addWidget(self.runtime_pill)
        card.body.addLayout(header)
        card.body.addWidget(divider())
        metrics = QGridLayout()
        metrics.setHorizontalSpacing(28)
        metrics.setVerticalSpacing(5)
        self.runtime_fields: dict[str, QLabel] = {}
        for index, key in enumerate(("ENDPOINT", "VERSION", "INSTALLED MODELS", "LOADED MODELS")):
            box = QVBoxLayout()
            box.addWidget(label(key, "MetricLabel"))
            value = label("—", "Mono", wrap=True)
            self.runtime_fields[key] = value
            box.addWidget(value)
            metrics.addLayout(box, index // 2, index % 2)
        card.body.addLayout(metrics)
        card.body.addWidget(divider())
        row = QHBoxLayout()
        row.addWidget(label(
            "Tool policy: model requests are validated host-side. Ollama itself has no filesystem grant.",
            "Faint", wrap=True,
        ), 1)
        self.test_runtime = button("Test runtime", variant="Secondary", on_click=self.test_runtime)
        row.addWidget(self.test_runtime)
        card.body.addLayout(row)
        return card

    def _servers_header(self) -> QWidget:
        card = Card(margins=(18, 14, 18, 14))
        row = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(2)
        copy.addWidget(label("MCP / TOOLS", "Eyebrow"))
        copy.addWidget(label("Model Context Protocol servers", "SectionTitle"))
        copy.addWidget(label(
            "Only exact approved tool names are exposed. Wildcards are rejected by policy.",
            "Muted", wrap=True,
        ))
        row.addLayout(copy, 1)
        self.mcp_support = Pill("SDK CHECK", "muted")
        row.addWidget(self.mcp_support)
        card.body.addLayout(row)
        return card

    def _trust_card(self) -> QWidget:
        card = Card(margins=(18, 15, 18, 15))
        card.body.addWidget(SectionHeader(
            "Trust boundaries",
            "What this workstation does and does not grant.",
        ))
        rows = (
            ("green", "Ollama connection", "localhost HTTP; no account or remote credential."),
            ("cyan", "Workspace tools", "Path-contained read, list, search and write; shell disabled."),
            ("amber", "MCP grants", "Per-server tool names only; environment secrets are referenced by name."),
            ("muted", "Network isolation", "Not an OS sandbox. Remote MCP servers and shell commands require separate review."),
        )
        for tone, title, copy in rows:
            line = QHBoxLayout()
            dot = StatusDot(tone, 7)
            line.addWidget(dot)
            line.addWidget(label(title, "Body"))
            line.addWidget(label(copy, "Faint", wrap=True), 1)
            wrap = QWidget()
            wrap.setLayout(line)
            card.body.addWidget(wrap)
        return card

    def _connect(self) -> None:
        self.context.events.data_changed.connect(self._data_changed)
        self.context.events.theme_changed.connect(self.refresh)

    def _data_changed(self, kind: str) -> None:
        if kind in {"settings", "models", "profiles"}:
            self.refresh()

    def refresh(self) -> None:
        online = self.context.ollama_online
        self.runtime_pill.setText("ONLINE" if online else "OFFLINE")
        self.runtime_pill.set_tone("green" if online else "red")
        self.runtime_fields["ENDPOINT"].setText(self.context.settings.ollama_base_url)
        self.runtime_fields["VERSION"].setText(self.context.ollama_version or "unavailable")
        self.runtime_fields["INSTALLED MODELS"].setText(str(len(self.context.models)))
        self.runtime_fields["LOADED MODELS"].setText(str(len(self.context.loaded_models)))
        has_sdk = importlib.util.find_spec("mcp") is not None
        self.mcp_support.setText("SDK READY" if has_sdk else "SDK OPTIONAL")
        self.mcp_support.set_tone("green" if has_sdk else "amber")
        self._render_servers()

    def _render_servers(self) -> None:
        while self.server_layout.count():
            item = self.server_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._server_cards = {}
        servers = self.context.settings.mcp_servers
        if not servers:
            self.server_layout.addWidget(label(
                "No MCP servers are configured. Add JSON in Settings when an external tool "
                "server should be available to a bounded agent.",
                "Muted", wrap=True,
            ))
            return
        for raw in servers:
            try:
                spec = MCPServerSpec.from_dict(raw)
            except Exception as exc:
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 5, 0, 5)
                lay.addWidget(Pill("INVALID", "red"))
                lay.addWidget(label(str(exc), "Faint", wrap=True), 1)
                self.server_layout.addWidget(row)
                continue
            self.server_layout.addWidget(self._server_row(spec, raw))

    def _server_row(self, spec: MCPServerSpec, raw: dict[str, Any]) -> QWidget:
        row = Card(inset=True, margins=(13, 10, 13, 10))
        top = QHBoxLayout()
        dot = StatusDot("green" if spec.enabled else "muted", 7)
        top.addWidget(dot)
        top.addWidget(label(spec.name, "CardTitle"))
        top.addWidget(Pill(spec.transport.replace("_", " "), "muted"))
        top.addWidget(Pill("ENABLED" if spec.enabled else "DISABLED",
                           "green" if spec.enabled else "muted"))
        top.addStretch(1)
        status = label("Not tested", "Faint")
        top.addWidget(status)
        test = button(
            "Test connection", variant="Ghost",
            on_click=lambda checked=False, spec=spec, raw=raw: self.test_server(spec, raw),
        )
        test.setEnabled(spec.enabled)
        top.addWidget(test)
        row.body.addLayout(top)
        detail = (
            f"{spec.command} {' '.join(spec.args)}" if spec.transport == "stdio" else spec.url
        )
        row.body.addWidget(label(detail or "No endpoint configured", "Mono", wrap=True))
        grants = ", ".join(spec.approved_tools) or "No tools approved"
        row.body.addWidget(label(
            f"Approved tools · {grants}  ·  timeout {spec.call_timeout_seconds:.0f}s",
            "Faint", wrap=True,
        ))
        self._server_cards[spec.name] = {"status": status, "raw": raw}
        return row

    def test_runtime(self) -> None:
        self.test_runtime.setEnabled(False)
        self.runtime_pill.setText("TESTING")
        self.runtime_pill.set_tone("cyan")

        def task() -> dict[str, Any]:
            client = self.context.client
            return {
                "online": client.health(),
                "version": client.version() if client.health() else "",
                "models": len(client.list_models()),
            }

        worker = FunctionWorker(task)
        worker.signals.result.connect(self._runtime_tested)
        worker.signals.error.connect(self._test_failed)
        worker.signals.finished.connect(lambda: self.test_runtime.setEnabled(True))
        self.context.pool.start(worker)

    def _runtime_tested(self, payload: dict[str, Any]) -> None:
        online = bool(payload.get("online"))
        self.context.ollama_online = online
        self.context.ollama_version = str(payload.get("version") or "")
        self.runtime_pill.setText("ONLINE" if online else "OFFLINE")
        self.runtime_pill.set_tone("green" if online else "red")
        self.runtime_fields["VERSION"].setText(self.context.ollama_version or "unavailable")
        self.runtime_fields["INSTALLED MODELS"].setText(str(payload.get("models") or 0))
        self.context.notify("Ollama runtime responded." if online else "Ollama did not answer.")

    def test_server(self, spec: MCPServerSpec, raw: dict[str, Any]) -> None:
        card = self._server_cards.get(spec.name)
        if card is None:
            return
        status = card["status"]
        status.setText("Connecting…")
        status.setStyleSheet(f"color:{COLORS['cyan']};")
        test_config = dict(raw)
        test_config["enabled"] = True

        def task() -> list[str]:
            with MCPToolGateway([test_config]) as gateway:
                return sorted(gateway.routes)

        worker = FunctionWorker(task)
        worker.signals.result.connect(lambda routes: self._server_tested(spec.name, routes))
        worker.signals.error.connect(lambda message: self._server_test_failed(spec.name, message))
        self.context.pool.start(worker)

    def _server_tested(self, name: str, routes: list[str]) -> None:
        card = self._server_cards.get(name)
        if card is None:
            return
        card["status"].setText(f"Connected · {len(routes)} tools")
        card["status"].setStyleSheet(f"color:{COLORS['green']};")
        self.context.notify(f"MCP {name}: {len(routes)} approved tool(s) discovered.")
        self.context.set_state("complete", f"MCP {name} connected")

    def _server_test_failed(self, name: str, message: str) -> None:
        card = self._server_cards.get(name)
        if card is None:
            return
        card["status"].setText(message.splitlines()[0][:90])
        card["status"].setStyleSheet(f"color:{COLORS['red']};")
        self.context.set_state("warning", f"MCP {name} failed to connect")
        self.context.notify(f"MCP {name} connection failed.")

    def _test_failed(self, message: str) -> None:
        self.runtime_pill.setText("FAILED")
        self.runtime_pill.set_tone("red")
        self.context.notify(message.splitlines()[0][:200])
        self.context.set_state("warning", "Runtime health check failed")

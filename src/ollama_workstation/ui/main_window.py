"""11vatedTech Intelligence desktop shell."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QThreadPool, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ollama_workstation.agents.memory import memory_store_for
from ollama_workstation.agents.tools import configure_command_sandbox
from ollama_workstation.config import AppPaths, load_settings, save_settings
from ollama_workstation.observability import configure_logging, log_event
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.profiling.profiler import ModelProfiler
from ollama_workstation.storage.repository import Repository
from ollama_workstation.ui.components import (
    Card,
    GroupLabel,
    LogoMark,
    NavButton,
    Pill,
    StatusDot,
    button,
    label,
)
from ollama_workstation.ui.intelligence_core import IntelligenceCore
from ollama_workstation.ui.pages.agents import AgentFoundryPage
from ollama_workstation.ui.pages.chat import ChatPage
from ollama_workstation.ui.pages.command_center import CommandCenterPage
from ollama_workstation.ui.pages.history import HistoryPage
from ollama_workstation.ui.pages.integrations import IntegrationHubPage
from ollama_workstation.ui.pages.laboratory import LaboratoryPage
from ollama_workstation.ui.pages.model_center import ModelCenterPage
from ollama_workstation.ui.pages.operations import OperationsPage
from ollama_workstation.ui.pages.settings import SettingsPage
from ollama_workstation.ui.pages.workforce import WorkforcePage
from ollama_workstation.ui.state import ActivityRegistry, AppContext, EventBus
from ollama_workstation.ui.telemetry import TelemetryPoller
from ollama_workstation.ui.theme import apply_theme
from ollama_workstation.ui.workers import FunctionWorker

PAGE_TITLES = {
    "command": "Command Center",
    "chat": "Advanced Chat",
    "models": "Model Intelligence",
    "agents": "Agent Foundry",
    "workforce": "Workforce Studio",
    "operations": "Agent Operations",
    "integrations": "Integration Hub",
    "laboratory": "System Laboratory",
    "history": "Run History",
    "settings": "Settings",
}

def _shortcut_for(key: str) -> str:
    """Ctrl+1..Ctrl+9 then Ctrl+0 for the tenth page."""
    index = list(PAGE_TITLES).index(key) + 1
    return f"Ctrl+{index % 10}" if index <= 10 else "no shortcut"


NAV_GROUPS = [
    ("OPERATE", (
        ("command", "◈   Command Center"),
        ("chat", "▣   Advanced Chat"),
        ("operations", "⌁   Agent Operations"),
    )),
    ("INTELLIGENCE", (
        ("models", "◇   Model Intelligence"),
        ("agents", "◎   Agent Foundry"),
        ("workforce", "⬡   Workforce Studio"),
    )),
    ("SYSTEM", (
        ("integrations", "⇄   Integration Hub"),
        ("laboratory", "⌬   System Laboratory"),
        ("history", "◷   Run History"),
        ("settings", "⚙   Settings"),
    )),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app)
        self.setWindowTitle("11vatedTech Intelligence — Ollama Workstation")
        self.resize(1480, 940)
        self.setMinimumSize(900, 700)
        self.paths = AppPaths.default()
        self.paths.ensure()
        configure_logging(self.paths.logs_dir)
        self.settings = load_settings(self.paths.config_file)
        self.client = OllamaClient(
            self.settings.ollama_base_url, self.settings.request_timeout_seconds
        )
        self.profiler = ModelProfiler(self.client, self.settings.keep_alive)
        self.repository = Repository(self.paths.database_file)
        self.memory_store = memory_store_for(self.repository, self.client, self.settings)
        configure_command_sandbox(self.settings.command_sandbox)
        self.bus = EventBus()
        self.activity = ActivityRegistry()
        self.context = AppContext(
            paths=self.paths,
            settings=self.settings,
            client=self.client,
            profiler=self.profiler,
            repository=self.repository,
            memory_store=self.memory_store,
            pool=QThreadPool.globalInstance(),
            events=self.bus,
            activity=self.activity,
        )
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, NavButton] = {}
        self._telemetry: TelemetryPoller | None = None
        self._inventory_worker: FunctionWorker | None = None
        self._loading_inventory = False
        self._shutdown_done = False
        self._last_page = "command"
        self._page_animation: QPropertyAnimation | None = None
        self._page_effect: QGraphicsOpacityEffect | None = None
        self._page_effect_target: QWidget | None = None
        self._build()
        self._wire()
        self._startup()
        self._apply_motion()

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._top_rail())
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(self.stack.sizePolicy().horizontalPolicy(), self.stack.sizePolicy().verticalPolicy())
        self.pages = {
            "command": CommandCenterPage(self.context),
            "chat": ChatPage(self.context),
            "models": ModelCenterPage(self.context),
            "agents": AgentFoundryPage(self.context),
            "workforce": WorkforcePage(self.context),
            "operations": OperationsPage(self.context),
            "integrations": IntegrationHubPage(self.context),
            "laboratory": LaboratoryPage(self.context),
            "history": HistoryPage(self.context),
            "settings": SettingsPage(self.context),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        right_layout.addWidget(self.stack, 1)
        outer.addWidget(right, 1)
        self.setCentralWidget(root)

    def _sidebar(self) -> QWidget:
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(236)
        layout = QVBoxLayout(self.sidebar)
        layout.setContentsMargins(14, 20, 14, 14)
        layout.setSpacing(2)
        brand = QHBoxLayout()
        brand.setSpacing(9)
        brand.addWidget(LogoMark(36))
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        word = QLabel("11VATED")
        word.setObjectName("BrandWord")
        meta = QLabel("INTELLIGENCE")
        meta.setObjectName("BrandMeta")
        brand_copy.addWidget(word)
        brand_copy.addWidget(meta)
        brand.addLayout(brand_copy, 1)
        layout.addLayout(brand)
        self.brand_meta = QLabel("LOCAL WORKSTATION · 0.2")
        self.brand_meta.setObjectName("Faint")
        self.brand_meta.setContentsMargins(45, 0, 0, 10)
        layout.addWidget(self.brand_meta)
        for group, entries in NAV_GROUPS:
            layout.addWidget(GroupLabel(group))
            for key, text in entries:
                nav = NavButton(text)
                nav.setAccessibleName(PAGE_TITLES[key])
                nav.setToolTip(f"Open {PAGE_TITLES[key]} ({_shortcut_for(key)})")
                nav.clicked.connect(lambda checked=False, target=key: self.navigate(target))
                self.nav_buttons[key] = nav
                layout.addWidget(nav)
        layout.addStretch(1)
        status = Card(inset=True, margins=(10, 9, 10, 9), spacing=3)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.sidebar_dot = StatusDot("muted", 6)
        top.addWidget(self.sidebar_dot)
        self.sidebar_runtime = label("OLLAMA OFFLINE", "MetricLabel")
        top.addWidget(self.sidebar_runtime, 1)
        status.body.addLayout(top)
        self.sidebar_detail = label("Waiting for the local runtime", "Faint", wrap=True)
        status.body.addWidget(self.sidebar_detail)
        self.sidebar_gpu = label("GPU telemetry pending", "Faint", wrap=True)
        status.body.addWidget(self.sidebar_gpu)
        layout.addWidget(status)
        footer = QHBoxLayout()
        footer.setContentsMargins(2, 4, 2, 0)
        footer.addWidget(label("LOCAL-FIRST", "Faint"))
        footer.addStretch(1)
        footer.addWidget(label("NO CLOUD", "Faint"))
        layout.addLayout(footer)
        return self.sidebar

    def _top_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("TopRail")
        rail.setFixedHeight(66)
        layout = QHBoxLayout(rail)
        layout.setContentsMargins(25, 9, 18, 9)
        layout.setSpacing(12)
        copy = QVBoxLayout()
        copy.setSpacing(0)
        self.page_kicker = label("11VATED / OPERATIONS", "Eyebrow")
        self.page_headline = label("Command Center", "CardTitle")
        copy.addWidget(self.page_kicker)
        copy.addWidget(self.page_headline)
        layout.addLayout(copy)
        layout.addStretch(1)
        self.status_message = label("Initializing local intelligence…", "Faint")
        self.status_message.setMaximumWidth(390)
        layout.addWidget(self.status_message)
        self.active_pill = Pill("0 ACTIVE", "muted")
        layout.addWidget(self.active_pill)
        self.health_pill = Pill("RUNTIME CHECK", "muted")
        layout.addWidget(self.health_pill)
        self.gpu_pill = Pill("GPU —", "muted")
        layout.addWidget(self.gpu_pill)
        self.top_refresh = button("Refresh", variant="Ghost", on_click=self.refresh_inventory,
                                  tooltip="Refresh local runtime and model inventory")
        self.top_refresh.setFixedWidth(72)
        layout.addWidget(self.top_refresh)
        self.top_core = IntelligenceCore(compact=True, caption=False)
        self.top_core.setFixedSize(53, 53)
        self.top_core.setAccessibleName("Global intelligence state")
        layout.addWidget(self.top_core)
        self.clock = label(datetime.now().strftime("%H:%M"), "Mono")
        self.clock.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.clock.setMinimumWidth(40)
        layout.addWidget(self.clock)
        timer = QTimer(self)
        timer.timeout.connect(lambda: self.clock.setText(datetime.now().strftime("%H:%M")))
        timer.start(15000)
        return rail

    def _wire(self) -> None:
        self.bus.navigate.connect(self.navigate)
        self.bus.status.connect(self._status)
        self.bus.core_state.connect(self._core_state)
        self.bus.data_changed.connect(self._data_changed)
        self.bus.refresh_inventory.connect(self.refresh_inventory)
        self.bus.theme_changed.connect(self._apply_motion)
        self.activity.changed.connect(self._activity_changed)
        for index, key in enumerate(PAGE_TITLES, start=1):
            if index > 10:
                break
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index % 10}"), self)
            shortcut.activated.connect(lambda target=key: self.navigate(target))
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh_inventory)

    def _startup(self) -> None:
        requested = self.settings.last_page if self.settings.last_page in PAGE_TITLES else "command"
        self.navigate(requested, persist=False)
        self._telemetry = TelemetryPoller(self.paths, interval_seconds=3.0)
        self._telemetry.sample_ready.connect(self._telemetry_received)
        self._telemetry.start()
        self.refresh_inventory()
        log_event("desktop_started", "Intelligence interface started")

    def navigate(self, key: str, *, persist: bool = True) -> None:
        if key not in self.pages:
            return
        previous = self.stack.currentWidget()
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self._last_page = key
        if previous is not page and self.isVisible():
            self._fade_page(page)
        for page_key, nav in self.nav_buttons.items():
            nav.blockSignals(True)
            nav.setChecked(page_key == key)
            nav.blockSignals(False)
        self.page_kicker.setText(f"11VATED / {self._group_for(key)}")
        self.page_headline.setText(PAGE_TITLES[key])
        if persist:
            self.settings.last_page = key
            try:
                save_settings(self.paths.config_file, self.settings)
            except OSError:
                pass
        refresh = getattr(page, "on_show", None)
        if callable(refresh):
            refresh()

    def _fade_page(self, page: QWidget) -> None:
        if self.settings.reduced_motion:
            return
        if self._page_animation is not None:
            self._page_animation.stop()
        if self._page_effect is not None:
            self._page_effect.setEnabled(False)
        if self._page_effect_target is not None:
            self._page_effect_target.setGraphicsEffect(None)  # type: ignore[arg-type]
        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.38)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(max(110, int(240 - self.settings.animation_intensity * 100)))
        animation.setStartValue(0.38)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish() -> None:
            if page.graphicsEffect() is effect:
                page.setGraphicsEffect(None)  # type: ignore[arg-type]
            if self._page_animation is animation:
                self._page_animation = None
            if self._page_effect is effect:
                self._page_effect = None
            if self._page_effect_target is page:
                self._page_effect_target = None

        animation.finished.connect(finish)
        self._page_animation = animation
        self._page_effect = effect
        self._page_effect_target = page
        animation.start()

    @staticmethod
    def _group_for(key: str) -> str:
        mapping = {
            "command": "OPERATIONS", "chat": "DIALOGUE", "operations": "OBSERVABILITY",
            "models": "EVIDENCE", "workforce": "ORGANIZATION", "integrations": "CONNECTIONS",
            "laboratory": "LABORATORY", "history": "MEMORY", "settings": "CONFIGURATION",
        }
        return mapping.get(key, "INTELLIGENCE")

    def _status(self, message: str) -> None:
        self.status_message.setText(message[:200])
        log_event("desktop_status", message[:300])

    def _core_state(self, state: str, detail: str) -> None:
        self.top_core.set_state(state, detail)
        if state in {"warning", "error", "disconnected"}:
            self.top_core.setStyleSheet("")

    def _activity_changed(self) -> None:
        operations = self.activity.active()
        self.active_pill.setText(f"{len(operations)} ACTIVE" if operations else "STANDBY")
        self.active_pill.set_tone("cyan" if operations else "muted")
        if operations:
            first = operations[0]
            self.status_message.setText(f"{first.kind}: {first.title}")

    def _data_changed(self, kind: str) -> None:
        if kind in {"models", "profiles"}:
            self._update_runtime_labels()
        if kind == "settings":
            self.refresh_inventory()

    def _telemetry_received(self, payload: dict[str, Any]) -> None:
        self.context.telemetry = payload
        self.bus.telemetry.emit(payload)
        gpus = payload.get("nvidia_gpus") or []
        gpu = gpus[0] if gpus else {}
        if gpu:
            utilization = gpu.get("utilization_percent")
            total = gpu.get("memory_total_mib")
            used = gpu.get("memory_used_mib")
            self.gpu_pill.setText(
                f"GPU {utilization if utilization is not None else '—'}%"
            )
            self.gpu_pill.set_tone("cyan" if utilization else "muted")
            self.sidebar_gpu.setText(
                f"{str(gpu.get('name') or 'NVIDIA').replace('NVIDIA ', '')}\n"
                f"{used or 0:,} / {total or 0:,} MiB"
            )
        else:
            self.gpu_pill.setText("GPU —")
            self.gpu_pill.set_tone("muted")
            self.sidebar_gpu.setText("No NVIDIA telemetry")

    def _update_runtime_labels(self) -> None:
        online = self.context.ollama_online
        self.sidebar_dot.set_tone("green" if online else "red")
        self.sidebar_runtime.setText("LOCAL RUNTIME ONLINE" if online else "OLLAMA OFFLINE")
        empirical = sum(
            1 for profile in self.context.profiles
            if any(item.status in {"passed", "partial", "failed"}
                   for item in profile.capabilities)
        )
        self.sidebar_detail.setText(
            f"{len(self.context.models)} models · {len(self.context.profiles)} metadata · "
            f"{empirical} empirical"
            if online else "Start Ollama to enable local inference."
        )
        self.health_pill.setText("RUNTIME ONLINE" if online else "RUNTIME OFFLINE")
        self.health_pill.set_tone("green" if online else "red")
        self.setWindowTitle(
            f"11vatedTech Intelligence — {self.context.ollama_version or 'offline'}"
        )

    def refresh_inventory(self) -> None:
        if self._loading_inventory:
            return
        self._loading_inventory = True
        self.top_refresh.setEnabled(False)
        self.top_refresh.setText("…")
        self.bus.status.emit("Refreshing local model inventory…")
        worker = FunctionWorker(self._load_runtime)
        self._inventory_worker = worker
        worker.signals.result.connect(self._runtime_loaded)
        worker.signals.error.connect(self._runtime_failed)
        worker.signals.finished.connect(self._runtime_finished)
        self.context.pool.start(worker)

    def _load_runtime(self) -> dict[str, Any]:
        # Inventory and health checks must stay responsive even when the chat
        # timeout is intentionally generous for long local generations.
        client = OllamaClient(
            self.settings.ollama_base_url,
            timeout=min(8.0, self.settings.request_timeout_seconds),
        )
        profiler = ModelProfiler(client, self.settings.keep_alive)
        online = False
        version = ""
        models: list[Any] = []
        running: list[Any] = []
        error = ""
        try:
            online = client.health()
            if online:
                version = client.version()
                models = profiler.inventory()
                running = client.running_models()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        try:
            profiles = self.repository.list_profiles()
        except Exception as exc:
            profiles = []
            error = error or f"{type(exc).__name__}: {exc}"
        return {
            "online": online, "version": version, "models": models,
            "running": running, "profiles": profiles, "error": error,
        }

    def _runtime_loaded(self, payload: dict[str, Any]) -> None:
        self.context.ollama_online = bool(payload.get("online"))
        self.context.ollama_version = str(payload.get("version") or "")
        self.context.models = list(payload.get("models") or [])
        self.context.loaded_models = list(payload.get("running") or [])
        self.context.profiles = list(payload.get("profiles") or [])
        self._update_runtime_labels()
        self.bus.data_changed.emit("models")
        self.bus.data_changed.emit("profiles")
        if self.context.ollama_online:
            self.bus.status.emit(
                f"Local runtime online · {len(self.context.models)} installed model(s)"
            )
            self.bus.core_state.emit(
                "standby", f"{len(self.context.models)} local models ready"
            )
        else:
            self.bus.status.emit(
                "Ollama is offline. Start the local runtime to enable inference."
            )
            self.bus.core_state.emit("disconnected", "Local runtime offline")

    def _runtime_failed(self, message: str) -> None:
        self.context.ollama_online = False
        self._update_runtime_labels()
        self.bus.status.emit(f"Inventory refresh failed: {message.splitlines()[0][:180]}")

    def _runtime_finished(self) -> None:
        self._loading_inventory = False
        self._inventory_worker = None
        self.top_refresh.setEnabled(True)
        self.top_refresh.setText("Refresh")
        self._activity_changed()

    def _apply_motion(self) -> None:
        for core in (self.top_core,):
            core.set_motion(
                reduced=self.settings.reduced_motion,
                intensity=self.settings.animation_intensity,
            )

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "sidebar"):
            return
        if self.width() < 1240:
            self.sidebar.setFixedWidth(208)
            self.brand_meta.hide()
        else:
            self.sidebar.setFixedWidth(236)
            self.brand_meta.show()
        if hasattr(self, "status_message"):
            self.status_message.setVisible(self.width() >= 1370)

    def closeEvent(self, event: QCloseEvent) -> None:
        operations = self.activity.active()
        if operations and not self._shutdown_done:
            response = QMessageBox.question(
                self,
                "Active intelligence work",
                f"{len(operations)} operation(s) are still active. Request cancellation and close? "
                "Durable checkpoints and journals remain available.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for operation in operations:
                self.activity.cancel(operation.operation_id)
        self._shutdown()
        event.accept()

    def _shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.settings.last_page = self._last_page
        try:
            save_settings(self.paths.config_file, self.settings)
        except OSError:
            pass
        if self._telemetry is not None:
            self._telemetry.stop()
            self._telemetry = None
        log_event("desktop_stopped", "Intelligence interface closed")


def run_desktop(*, smoke_test: bool = False) -> int:
    """Start the desktop shell. ``smoke_test`` builds every page, renders one
    frame and exits 0, so an installed package can be proven to launch."""
    instance = QApplication.instance()
    app = instance if isinstance(instance, QApplication) else QApplication([])
    app.setApplicationName("11vatedTech Intelligence")
    app.setOrganizationName("11vatedTech")
    apply_theme(app)
    window = MainWindow()
    window.show()
    if smoke_test:
        for key in list(window.pages):
            window.navigate(key, persist=False)
            app.processEvents()
        print(f"smoke-test ok: {len(window.pages)} pages rendered", flush=True)
        window.close()
        app.processEvents()
        return 0
    return app.exec()

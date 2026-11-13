"""Shared desktop application state, event bus and activity registry."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThreadPool, Signal

from ollama_workstation.agents.memory import MemoryStore, memory_store_for
from ollama_workstation.config import AppPaths, Settings
from ollama_workstation.models.model_intelligence import ModelIntelligence
from ollama_workstation.models.schemas import ModelProfile, ModelRef
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.profiling.profiler import ModelProfiler
from ollama_workstation.storage.repository import Repository


class EventBus(QObject):
    """Application-wide signals that keep visual state out of domain state."""

    core_state = Signal(str, str)
    status = Signal(str)
    navigate = Signal(str)
    toast = Signal(str, str)
    data_changed = Signal(str)
    telemetry = Signal(dict)
    theme_changed = Signal()
    compose_workforce = Signal(str)
    refresh_inventory = Signal()


@dataclass(slots=True)
class ActiveOperation:
    operation_id: str
    kind: str
    title: str
    detail: str
    started_at: str
    cancel_event: Event | None = None
    cancel: Callable[[], None] | None = None
    page: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ActivityRegistry(QObject):
    """Tracks real in-flight work so every part of the UI reports the same truth."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._operations: dict[str, ActiveOperation] = {}

    def begin(
        self,
        kind: str,
        title: str,
        detail: str = "",
        *,
        cancel_event: Event | None = None,
        cancel: Callable[[], None] | None = None,
        page: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        operation_id = str(uuid.uuid4())
        self._operations[operation_id] = ActiveOperation(
            operation_id=operation_id,
            kind=kind,
            title=title,
            detail=detail,
            started_at=datetime.now(UTC).isoformat(),
            cancel_event=cancel_event,
            cancel=cancel,
            page=page,
            metadata=dict(metadata or {}),
        )
        self.changed.emit()
        return operation_id

    def update(self, operation_id: str, detail: str | None = None, title: str | None = None) -> None:
        operation = self._operations.get(operation_id)
        if operation is None:
            return
        if detail is not None:
            operation.detail = detail
        if title is not None:
            operation.title = title
        self.changed.emit()

    def finish(self, operation_id: str) -> None:
        if self._operations.pop(operation_id, None) is not None:
            self.changed.emit()

    def cancel(self, operation_id: str) -> None:
        operation = self._operations.get(operation_id)
        if operation is None:
            return
        if operation.cancel_event is not None:
            operation.cancel_event.set()
        if operation.cancel is not None:
            operation.cancel()
        operation.detail = "Stop requested; waiting for a safe boundary"
        self.changed.emit()

    def active(self) -> list[ActiveOperation]:
        return list(self._operations.values())

    def get(self, operation_id: str) -> ActiveOperation | None:
        return self._operations.get(operation_id)

    def clear_finished(self) -> None:
        self._operations.clear()
        self.changed.emit()


@dataclass(slots=True)
class AppContext:
    paths: AppPaths
    settings: Settings
    client: OllamaClient
    profiler: ModelProfiler
    repository: Repository
    memory_store: MemoryStore
    pool: QThreadPool
    events: EventBus
    activity: ActivityRegistry
    models: list[ModelRef] = field(default_factory=list)
    profiles: list[ModelProfile] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    ollama_version: str = ""
    ollama_online: bool = False
    loaded_models: list[dict[str, Any]] = field(default_factory=list)

    def installed(self, name: str) -> ModelRef | None:
        return next((item for item in self.models if item.name == name), None)

    def profile_for(self, name: str) -> ModelProfile | None:
        installed = self.installed(name)
        if installed is not None:
            exact = self.repository.get_profile(name, installed.digest)
            if exact is not None:
                return exact
        return next((item for item in self.profiles if item.model_name == name), None)

    def intelligence_for(self, name: str) -> ModelIntelligence | None:
        profile = self.profile_for(name)
        if profile is None or not profile.intelligence:
            return None
        try:
            return ModelIntelligence.from_dict(profile.intelligence)
        except (TypeError, ValueError):
            return None

    def reload_profiles(self) -> list[ModelProfile]:
        self.profiles = self.repository.list_profiles()
        return self.profiles

    def reload_models(self) -> list[ModelRef]:
        self.models = self.profiler.inventory()
        return self.models

    def navigate(self, page: str) -> None:
        self.events.navigate.emit(page)

    def set_state(self, state: str, detail: str = "") -> None:
        self.events.core_state.emit(state, detail)

    def notify(self, message: str) -> None:
        self.events.status.emit(message)

    def workspace_path(self) -> Path:
        configured = self.settings.workspace_root.strip()
        value = Path(configured).expanduser() if configured else self.paths.workspaces_dir
        value.mkdir(parents=True, exist_ok=True)
        return value.resolve()

    def refresh_settings(self) -> None:
        self.client = OllamaClient(
            self.settings.ollama_base_url, self.settings.request_timeout_seconds
        )
        self.profiler = ModelProfiler(self.client, self.settings.keep_alive)
        self.memory_store = memory_store_for(self.repository, self.client, self.settings)
        self.events.theme_changed.emit()
        self.events.data_changed.emit("settings")

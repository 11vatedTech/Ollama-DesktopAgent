"""Application configuration and persistent local paths."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir


@dataclass(slots=True)
class Settings:
    ollama_base_url: str = "http://127.0.0.1:11434"
    request_timeout_seconds: float = 120.0
    profile_timeout_seconds: float = 90.0
    enable_remote_model_research: bool = False
    max_agent_steps: int = 12
    allow_shell_tools: bool = False
    allowed_commands: tuple[str, ...] = ("git", "python", "pytest", "ruff")
    workspace_root: str = ""
    keep_alive: str = "5m"
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    reduced_motion: bool = False
    animation_intensity: float = 0.8
    last_page: str = "command"
    agent_presets: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if "allowed_commands" in allowed:
            allowed["allowed_commands"] = tuple(str(x) for x in allowed["allowed_commands"])
        if "mcp_servers" in allowed:
            servers = allowed["mcp_servers"]
            if not isinstance(servers, list) or not all(isinstance(item, dict) for item in servers):
                raise ValueError("mcp_servers must be a list of JSON objects")
            allowed["mcp_servers"] = [dict(item) for item in servers]
        if "agent_presets" in allowed:
            presets = allowed["agent_presets"]
            if not isinstance(presets, list) or not all(isinstance(item, dict) for item in presets):
                raise ValueError("agent_presets must be a list of JSON objects")
            allowed["agent_presets"] = [dict(item) for item in presets]
        if "animation_intensity" in allowed:
            try:
                allowed["animation_intensity"] = max(
                    0.2, min(1.0, float(allowed["animation_intensity"]))
                )
            except (TypeError, ValueError):
                allowed.pop("animation_intensity")
        return cls(**allowed)


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    config_file: Path
    database_file: Path
    workspaces_dir: Path
    profiles_dir: Path
    logs_dir: Path

    @classmethod
    def default(cls) -> AppPaths:
        # OLLAMA_WORKSTATION_HOME relocates all state (portable installs, isolated tests).
        override = os.environ.get("OLLAMA_WORKSTATION_HOME", "").strip()
        root = (Path(override).expanduser() if override
                else Path(user_data_dir("11vated-Ollama-Workstation", "11vatedTech")))
        return cls(root, root / "settings.json", root / "workstation.sqlite3",
                   root / "workspaces", root / "profiles", root / "logs")

    def ensure(self) -> None:
        for path in (self.root, self.workspaces_dir, self.profiles_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Settings root must be a JSON object")
        return Settings.from_dict(payload)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return Settings()


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    temporary.replace(path)

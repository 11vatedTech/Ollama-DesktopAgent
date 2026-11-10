"""Application configuration and persistent local paths."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        if "allowed_commands" in allowed:
            allowed["allowed_commands"] = tuple(str(x) for x in allowed["allowed_commands"])
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
    def default(cls) -> "AppPaths":
        root = Path(user_data_dir("11vated-Ollama-Workstation", "11vatedTech"))
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

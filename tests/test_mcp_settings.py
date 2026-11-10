from __future__ import annotations

import json

import pytest

from ollama_workstation.config import Settings, load_settings, save_settings


def test_mcp_server_settings_default_to_empty_and_disabled() -> None:
    settings = Settings()
    assert settings.mcp_servers == []
    loaded = Settings.from_dict({})
    assert loaded.mcp_servers == []


def test_mcp_server_settings_round_trip_without_secret_values(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings(mcp_servers=[{
        "name": "workspace",
        "transport": "stdio",
        "enabled": False,
        "command": "python",
        "args": ["-m", "workspace_server"],
        "env_refs": {"API_TOKEN": "WORKSPACE_MCP_TOKEN"},
        "approved_tools": ["read_file", "list_files"],
    }])
    save_settings(path, settings)
    text = path.read_text(encoding="utf-8")
    assert "WORKSPACE_MCP_TOKEN" in text
    assert "secret-value" not in text
    loaded = load_settings(path)
    assert loaded.mcp_servers == settings.mcp_servers


@pytest.mark.parametrize("value", [None, {}, "invalid", [1], [{"name": "bad"}, "bad"]])
def test_settings_reject_malformed_mcp_server_collection(value) -> None:
    with pytest.raises(ValueError, match="mcp_servers"):
        Settings.from_dict({"mcp_servers": value})


def test_mcp_server_environment_refs_are_not_resolved_during_settings_save(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = Settings(mcp_servers=[{
        "name": "private_server",
        "transport": "stdio",
        "enabled": False,
        "command": "python",
        "env_refs": {"SERVICE_TOKEN": "HOST_SERVICE_TOKEN"},
        "approved_tools": [],
    }])
    save_settings(path, settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["mcp_servers"][0]["env_refs"] == {"SERVICE_TOKEN": "HOST_SERVICE_TOKEN"}

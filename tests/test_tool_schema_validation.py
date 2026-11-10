from pathlib import Path

import pytest

from ollama_workstation.agents.tools import ToolError, WorkspaceTools


def test_host_rejects_missing_and_extra_tool_arguments(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError, match="Missing required"):
        tools.invoke("read_file", {})
    with pytest.raises(ToolError, match="Unexpected argument"):
        tools.invoke("read_file", {"path": "notes.txt", "outside_scope": True})


def test_host_rejects_wrong_argument_types_before_execution(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError, match="must be string"):
        tools.invoke("list_files", {"path": 42})


def test_host_rejects_disabled_shell_tool_even_if_model_requests_it(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path, allow_shell=False)
    with pytest.raises(ToolError, match="not registered or enabled"):
        tools.invoke("run_command", {"command": "python", "args": ["--version"]})

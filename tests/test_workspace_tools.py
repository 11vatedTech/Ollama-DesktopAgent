from pathlib import Path
import pytest
from ollama_workstation.agents.tools import ToolError, WorkspaceTools

def test_workspace_write_read_and_list(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    result = tools.write_file("notes/hello.txt", "hello world")
    assert result["bytes_written"] == 11
    assert tools.read_file("notes/hello.txt")["content"] == "hello world"
    assert tools.list_files("notes")["entries"][0]["name"] == "hello.txt"

def test_workspace_blocks_parent_traversal(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError, match="outside"):
        tools.read_file("../secret.txt")

def test_workspace_requires_approval_to_overwrite(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path)
    tools.write_file("same.txt", "first")
    with pytest.raises(ToolError, match="not approved"):
        tools.write_file("same.txt", "second")

def test_command_execution_is_disabled_by_default(tmp_path: Path) -> None:
    tools = WorkspaceTools(tmp_path, allow_shell=False, allowed_commands=("python",), approval=lambda _: True)
    with pytest.raises(ToolError, match="disabled"):
        tools.run_command("python", ["--version"])

def test_command_execution_requires_allowlist_and_approval(tmp_path: Path) -> None:
    denied = WorkspaceTools(tmp_path, allow_shell=True, allowed_commands=("python",), approval=lambda _: False)
    with pytest.raises(ToolError, match="not approved"):
        denied.run_command("python", ["--version"])
    allowed = WorkspaceTools(tmp_path, allow_shell=True, allowed_commands=("python",), approval=lambda _: True)
    result = allowed.run_command("python", ["--version"])
    assert result["return_code"] == 0

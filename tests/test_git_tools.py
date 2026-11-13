"""Read-only git tools: opt-in, contained, and unable to run repository-controlled code."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ollama_workstation.agentification.agents import AgentDefinition, Permissions
from ollama_workstation.agents.tools import ToolError, WorkspaceTools

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"],
                 ["config", "user.name", "Tester"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("print('v1')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo, check=True,
                   capture_output=True)
    return repo


def test_git_tools_are_absent_unless_granted(tmp_path: Path) -> None:
    names = {item["function"]["name"] for item in WorkspaceTools(_repo(tmp_path)).definitions()}
    assert not names & {"git_status", "git_diff", "git_log"}
    with pytest.raises(ToolError):
        WorkspaceTools(tmp_path / "repo").invoke("git_status", {})


def test_status_diff_and_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
    tools = WorkspaceTools(repo, allow_git=True)
    assert "M app.py" in tools.invoke("git_status", {})["output"]
    diff = tools.invoke("git_diff", {"path": "app.py"})["output"]
    assert "-print('v1')" in diff and "+print('v2')" in diff
    assert "initial commit" in tools.invoke("git_log", {"limit": 5})["output"]


def test_git_diff_path_cannot_escape_the_workspace(tmp_path: Path) -> None:
    tools = WorkspaceTools(_repo(tmp_path), allow_git=True)
    with pytest.raises(ToolError):
        tools.invoke("git_diff", {"path": "../outside.txt"})


def test_repository_configured_fsmonitor_and_textconv_do_not_execute(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    marker = tmp_path / "executed.txt"
    hook = tmp_path / "hook.sh"
    hook.write_bytes(f'#!/bin/sh\necho pwned > "{marker.as_posix()}"\ncat "$1"\n'.encode())
    subprocess.run(["git", "config", "core.fsmonitor", f"sh {hook.as_posix()}"], cwd=repo,
                   check=True)
    subprocess.run(["git", "config", "diff.evil.textconv", f"sh {hook.as_posix()}"], cwd=repo,
                   check=True)
    (repo / ".gitattributes").write_text("*.py diff=evil\n", encoding="utf-8")
    (repo / "app.py").write_text("print('v3')\n", encoding="utf-8")
    # Control: plain git really does run the repository-configured textconv.
    subprocess.run(["git", "--no-pager", "-c", "core.fsmonitor=false", "diff"], cwd=repo,
                   capture_output=True, check=False)
    if not marker.exists():
        pytest.skip("this git build does not run textconv via sh; control not established")
    marker.unlink()
    tools = WorkspaceTools(repo, allow_git=True)
    tools.invoke("git_status", {})
    tools.invoke("git_diff", {})
    assert not marker.exists()


def test_agent_permission_round_trips_and_keeps_old_digests(tmp_path: Path) -> None:
    base = dict(agent_id="a", version=1, name="Git Reader", model_name="m", model_digest="d",
                role="general_assistant", role_title="R")
    plain = AgentDefinition(**base)
    assert "git" not in plain.to_dict()["permissions"]
    granted = AgentDefinition(**base, permissions=Permissions(git=True))
    assert AgentDefinition.from_dict(granted.to_dict()).permissions.git is True
    assert granted.config_sha256 != plain.config_sha256

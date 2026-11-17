"""edit_file: exact-string edits for coding agents, granted and qualified separately."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ollama_workstation.agentification import harness as h
from ollama_workstation.agentification.agents import Permissions
from ollama_workstation.agentification.commands import _qualification_factory
from ollama_workstation.agentification.gate import QUALIFIABLE_TOOLS
from ollama_workstation.agentification.tasks import instantiate
from ollama_workstation.agentification.verifier import VerificationEngine
from ollama_workstation.agents.tools import ToolError, WorkspaceTools

PRE_WEB_SUITE_SHA256_PREFIX = "6dc7b6886dbe"


def _tools(tmp_path: Path, *, approve: bool = True) -> WorkspaceTools:
    return WorkspaceTools(tmp_path, allow_edit=True, approval=lambda _d: approve)


def test_edit_replaces_one_unique_passage(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = _tools(tmp_path).invoke("edit_file", {"path": "a.py", "old_string": "y = 2",
                                                   "new_string": "y = 3"})
    assert result["replacements"] == 1
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 3\n"


@pytest.mark.parametrize(("old", "message"), [
    ("z = 9", "not found"),
    ("= ", "matches 2 places"),
])
def test_missing_or_ambiguous_match_changes_nothing(tmp_path: Path, old: str, message: str) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    with pytest.raises(ToolError, match=message):
        _tools(tmp_path).edit_file("a.py", old, "q")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\ny = 2\n"


def test_replace_all_and_crlf_files_keep_their_line_endings(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"a\r\nb\r\na\r\n")
    result = _tools(tmp_path).edit_file("a.txt", "a\nb", "c\nd")
    assert result["replacements"] == 1
    assert (tmp_path / "a.txt").read_bytes() == b"c\r\nd\r\na\r\n"
    _tools(tmp_path).edit_file("a.txt", "a", "z", replace_all=True)
    assert (tmp_path / "a.txt").read_bytes() == b"c\r\nd\r\nz\r\n"


def test_edit_needs_the_grant_and_the_overwrite_approval(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert "edit_file" not in {d["function"]["name"] for d in WorkspaceTools(tmp_path).definitions()}
    with pytest.raises(ToolError):
        WorkspaceTools(tmp_path, approval=lambda _d: True).invoke(
            "edit_file", {"path": "a.py", "old_string": "1", "new_string": "2"})
    with pytest.raises(ToolError, match="not approved"):
        _tools(tmp_path, approve=False).edit_file("a.py", "1", "2")
    with pytest.raises(ToolError, match="outside"):
        _tools(tmp_path).edit_file("../a.py", "1", "2")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 1\n"


def test_permission_round_trips_and_is_omitted_when_off() -> None:
    assert "edit" not in Permissions().to_dict()  # existing agent digests unchanged
    assert Permissions(edit=True).to_dict()["edit"] is True


def _effective(factory: h.ToolFactory) -> tuple[h.TaskSpec, ...]:
    with tempfile.TemporaryDirectory() as directory:
        names = {d["function"]["name"] for d in factory(Path(directory) / "w", None).definitions()}
    return tuple(spec for spec in h.core_suite() if all(t in names for t in spec.requires_tools))


def test_existing_fingerprints_unchanged_and_edit_catalogue_adds_the_task() -> None:
    for factory in (h.default_tool_factory, h.shell_tool_factory):
        assert h.suite_fingerprint(_effective(factory)).startswith(PRE_WEB_SUITE_SHA256_PREFIX)
    tasks = _effective(_qualification_factory(False, False, edit=True))
    assert tasks[-1].task_id == "code_edit"
    assert "edit_file" in QUALIFIABLE_TOOLS
    with pytest.raises(ValueError):
        _qualification_factory(False, True, edit=True)


def test_code_edit_task_passes_only_on_an_exact_edit(tmp_path: Path) -> None:
    spec = next(item for item in h.core_suite() if item.task_id == "code_edit")
    instance = instantiate(spec, seed=7)
    workspace = tmp_path / "w"
    for relative, content in instance.files.items():
        (workspace / relative).parent.mkdir(parents=True, exist_ok=True)
        (workspace / relative).write_text(content, encoding="utf-8")
    baseline = {"app/settings.py": (workspace / "app/settings.py").read_text(encoding="utf-8")}
    tools = _tools(workspace)
    old_line = next(line for line in baseline["app/settings.py"].splitlines()
                    if line.startswith("TIMEOUT_SECONDS"))
    new_value = instance.objective.split("TIMEOUT_SECONDS to ")[1].split(" ")[0]
    tools.edit_file("app/settings.py", old_line, f"TIMEOUT_SECONDS = {new_value}")
    checks = [c for c in instance.checks if c["kind"] == "file_equals"]
    assert VerificationEngine(workspace).verify(checks).passed
    # Rewriting the file and dropping a line, the failure edit_file exists to prevent, fails.
    (workspace / "app/settings.py").write_text(
        baseline["app/settings.py"].replace("RETRIES = 3\n", "").replace(
            old_line, f"TIMEOUT_SECONDS = {new_value}"), encoding="utf-8")
    assert not VerificationEngine(workspace).verify(checks).passed


def test_permission_layer_refuses_edit_without_both_grants(tmp_path: Path) -> None:
    from ollama_workstation.agentification.agents import GrantedTools

    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    inner = WorkspaceTools(tmp_path, allow_edit=True, approval=lambda _d: True)
    arguments = {"path": "a.py", "old_string": "1", "new_string": "2"}
    for permissions in (Permissions(edit=False), Permissions(edit=True, write=False)):
        with pytest.raises(ToolError, match="edit and write"):
            GrantedTools(inner, permissions).invoke("edit_file", arguments)
    GrantedTools(inner, Permissions(edit=True)).invoke("edit_file", arguments)
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 2\n"

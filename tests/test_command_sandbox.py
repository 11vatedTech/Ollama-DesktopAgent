"""AppContainer command sandbox (Windows): real OS, positive and negative controls.

Each probe runs the same script twice through ``WorkspaceTools.run_command``:
once in the AppContainer and once with the Job Object only. The job-only run
is the negative control: it shows the probe *would* succeed without the
sandbox, so a denial in the sandboxed run is caused by the sandbox.
"""

from __future__ import annotations

import ast
import os
import socket
import sys
from pathlib import Path

import pytest

from ollama_workstation.agents.tools import ToolError, WorkspaceTools, configure_command_sandbox

pytestmark = pytest.mark.skipif(os.name != "nt", reason="AppContainer is Windows-only")

PROBE = r'''
import os, socket, sys, tempfile, pathlib
result = {}
def attempt(name, action):
    try:
        result[name] = action()
    except Exception as exc:
        result[name] = "denied:" + type(exc).__name__
attempt("write_workspace", lambda: pathlib.Path("made.txt").write_text("ok") and "ok")
attempt("read_workspace", lambda: pathlib.Path("input.txt").read_text().strip())
attempt("temp_file", lambda: tempfile.NamedTemporaryFile(delete=True) and "ok")
attempt("read_outside", lambda: pathlib.Path(sys.argv[1]).read_text().strip())
attempt("write_outside", lambda: pathlib.Path(sys.argv[2]).write_text("x") and "ok")
def connect():
    socket.create_connection(("127.0.0.1", int(sys.argv[3])), timeout=3).close()
    return "connected"
attempt("loopback", connect)
print(repr(result))
'''


def _python() -> Path:
    return Path(sys.executable).resolve()


def _run_probe(tmp_path: Path, sandbox: str) -> dict[str, str]:
    workspace = tmp_path / f"ws-{sandbox}"
    outside = tmp_path / f"outside-{sandbox}"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "probe.py").write_text(PROBE, encoding="utf-8")
    (workspace / "input.txt").write_text("inside", encoding="utf-8")
    secret = outside / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    try:
        tools = WorkspaceTools(workspace, allow_shell=True, allowed_commands=(str(_python()),),
                               approval=lambda _description: True, sandbox=sandbox)
        output = tools.run_command(_python().name, [
            "probe.py", str(secret), str(outside / "planted.txt"),
            str(listener.getsockname()[1])])
    finally:
        listener.close()
    assert output["return_code"] == 0, output
    return ast.literal_eval(output["stdout"].strip().splitlines()[-1])


def test_sandboxed_command_is_confined_to_the_workspace_and_offline(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, "appcontainer")
    assert result["write_workspace"] == "ok"
    assert result["read_workspace"] == "inside"
    assert result["temp_file"] == "ok"
    assert result["read_outside"].startswith("denied:")
    assert result["write_outside"].startswith("denied:")
    assert result["loopback"].startswith("denied:")
    assert not (tmp_path / "outside-appcontainer" / "planted.txt").exists()


def test_negative_control_job_only_command_has_user_access(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, "job")
    assert result["read_outside"] == "TOP SECRET"
    assert result["write_outside"] == "ok"
    assert result["loopback"] == "connected"


def test_sandbox_failure_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ollama_workstation.agents import sandbox

    def broken(*_args: object, **_kwargs: object) -> None:
        raise sandbox.SandboxError("profile unavailable")

    monkeypatch.setattr(sandbox, "launch", broken)
    (tmp_path / "x.py").write_text("print('ran')", encoding="utf-8")
    tools = WorkspaceTools(tmp_path, allow_shell=True, allowed_commands=(str(_python()),),
                           approval=lambda _description: True, sandbox="appcontainer")
    with pytest.raises(ToolError, match="sandbox could not be established"):
        tools.run_command(_python().name, ["x.py"])


def test_sandbox_setting_is_validated() -> None:
    with pytest.raises(ValueError):
        configure_command_sandbox("none")
    with pytest.raises(ValueError):
        WorkspaceTools(Path.cwd(), sandbox="off")

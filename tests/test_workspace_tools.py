import sys
import time
from pathlib import Path
from threading import Event, Thread

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



def test_command_rejects_model_supplied_executable_paths(tmp_path: Path) -> None:
    tools = WorkspaceTools(
        tmp_path, allow_shell=True, allowed_commands=("python",), approval=lambda _: True
    )
    with pytest.raises(ToolError, match="not allow-listed"):
        tools.run_command(sys.executable, ["--version"])


def test_command_cancellation_stops_the_running_process(tmp_path: Path) -> None:
    cancelled = Event()
    tools = WorkspaceTools(
        tmp_path, allow_shell=True, allowed_commands=("python",),
        approval=lambda _: True, cancel_event=cancelled,
    )
    trigger = Thread(target=lambda: (time.sleep(0.4), cancelled.set()), daemon=True)
    trigger.start()
    with pytest.raises(ToolError, match="cancelled"):
        tools.run_command("python", ["-c", "import time; time.sleep(30)"])
    trigger.join(timeout=2)
    assert cancelled.is_set()



@pytest.mark.skipif(sys.platform != "linux", reason="Linux process-group regression test")
def test_command_timeout_kills_descendant_after_parent_exits(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    tools = WorkspaceTools(
        tmp_path, allow_shell=True, allowed_commands=("python",), approval=lambda _: True
    )
    tools.COMMAND_TIMEOUT_SECONDS = 0.6
    tools.COMMAND_TERMINATION_GRACE_SECONDS = 0.1
    child_code = (
        "import os,sys,time; "
        "open(sys.argv[1], 'w', encoding='utf-8').write(str(os.getpid())); "
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]])"
    )
    with pytest.raises(ToolError, match="exceeded"):
        tools.run_command("python", ["-c", parent_code, child_code, str(pid_file)])
    assert pid_file.exists()
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    state = None
    for _ in range(30):
        try:
            with open(f"/proc/{child_pid}/stat", encoding="utf-8") as stream:
                state = stream.read().split()[2]
        except FileNotFoundError:
            state = None
        if state in (None, "Z"):
            break
        time.sleep(0.05)
    assert state in (None, "Z")

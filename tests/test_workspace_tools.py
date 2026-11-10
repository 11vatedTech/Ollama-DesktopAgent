import sys
import time
from io import StringIO
from pathlib import Path
from threading import Event, Thread

import pytest

from ollama_workstation.agents.tools import ToolError, WorkspaceTools, _BoundedPipeCapture


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



def test_workspace_blocks_credentials_and_private_key_material(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_TOKEN=secret-value", encoding="utf-8")
    (tmp_path / ".env.example").write_text("API_TOKEN=replace-me", encoding="utf-8")
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_ed25519").write_text("PRIVATE KEY", encoding="utf-8")
    (tmp_path / "service.pem").write_text("PRIVATE CERTIFICATE BUNDLE", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)

    with pytest.raises(ToolError, match="credential or private-key"):
        tools.read_file(".env")
    with pytest.raises(ToolError, match="credential or private-key"):
        tools.read_file(".ssh/id_ed25519")
    with pytest.raises(ToolError, match="credential or private-key"):
        tools.write_file(".env", "SHOULD_NOT_WRITE=true")
    with pytest.raises(ToolError, match="credential or private-key"):
        tools.list_files(".ssh")
    assert tools.read_file(".env.example")["content"] == "API_TOKEN=replace-me"
    visible_names = {item["name"] for item in tools.list_files()["entries"]}
    assert ".env" not in visible_names
    assert ".ssh" not in visible_names
    assert "service.pem" not in visible_names


def test_workspace_search_never_returns_secrets(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("TOP_SECRET=do-not-read", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ordinary project note", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    result = tools.search_text("TOP_SECRET")
    assert result["matches"] == []



def test_text_outputs_redact_credentials_before_model_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = "\n".join([
        "API_KEY=plain-api-secret",
        '{"client_secret": "json-client-secret", "mode": "local"}',
        "Authorization: Bearer bearer-token-value-123456789",
        "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF",
        "github=ghp_123456789012345678901234567890123456",
    ])
    (workspace / "config.txt").write_text(source, encoding="utf-8")
    tools = WorkspaceTools(workspace)
    result = tools.read_file("config.txt")
    assert result["secrets_redacted"] >= 5
    assert "plain-api-secret" not in result["content"]
    assert "json-client-secret" not in result["content"]
    assert "bearer-token-value" not in result["content"]
    assert "AKIA1234567890ABCDEF" not in result["content"]
    assert "ghp_123456789012345678901234567890123456" not in result["content"]
    assert "mode" in result["content"]


def test_search_and_command_output_redact_sensitive_values(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text(
        "API_TOKEN=search-secret-value-123456\\n",
        encoding="utf-8",
    )
    tools = WorkspaceTools(tmp_path)
    search = tools.search_text("API_TOKEN")
    assert search["matches"]
    assert "search-secret-value" not in search["matches"][0]["text"]
    safe_stdout, count = tools.redact_sensitive_text("PASSWORD=command-secret-value")
    assert count == 1
    assert "command-secret-value" not in safe_stdout



def test_command_stdout_and_approval_metadata_redact_tokens(tmp_path: Path) -> None:
    approval_prompts: list[str] = []
    tools = WorkspaceTools(
        tmp_path,
        allow_shell=True,
        allowed_commands=("python",),
        approval=lambda prompt: approval_prompts.append(prompt) or True,
    )
    secret = "run-secret-value-123456"
    result = tools.run_command(
        "python", ["-c", f"print('API_TOKEN={secret}')"]
    )
    assert result["return_code"] == 0
    assert secret not in result["stdout"]
    assert secret not in result["command"][1]
    assert secret not in approval_prompts[0]
    assert result["secrets_redacted"] >= 1



def test_subprocess_pipe_capture_retains_a_bounded_tail() -> None:
    capture = _BoundedPipeCapture(max_chars=10)
    capture.drain(StringIO("0123456789abcdefghijklmnop"))
    assert capture.value() == "ghijklmnop"
    assert len(capture.value()) == 10

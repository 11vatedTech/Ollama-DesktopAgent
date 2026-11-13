"""Job Object containment of agent commands, exercised against the real Windows kernel."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ollama_workstation.agents.containment import JobLimits
from ollama_workstation.agents.tools import WorkspaceTools

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows-only")


def _tools(workspace: Path, **limits: int) -> WorkspaceTools:
    tools = WorkspaceTools(workspace, allow_shell=True, allowed_commands=(sys.executable,),
                           approval=lambda _description: True)
    tools.allowed_command_paths = {"python": Path(sys.executable).resolve()}
    if limits:
        tools.containment_limits = JobLimits(**limits)
    return tools


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                         text=True, check=False).stdout
    return str(pid) in out


def test_background_descendant_cannot_outlive_the_command(tmp_path: Path) -> None:
    script = tmp_path / "spawn.py"
    script.write_text(
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],\n"
        "    creationflags=0x00000008)  # DETACHED_PROCESS: escapes taskkill /T\n"
        "open('child.pid', 'w').write(str(child.pid))\n"
        "print('spawned')\n", encoding="utf-8")
    started = time.monotonic()
    result = _tools(tmp_path).run_command("python", [str(script)])
    assert result["return_code"] == 0 and "spawned" in result["stdout"]
    assert time.monotonic() - started < 30  # did not wait for the 120 s sleeper
    pid = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid), "a detached descendant survived the job"


def test_process_count_limit_is_enforced(tmp_path: Path) -> None:
    script = tmp_path / "bomb.py"
    script.write_text(
        "import subprocess, sys\n"
        "made = 0\n"
        "procs = []\n"
        "for _ in range(10):\n"
        "    try:\n"
        "        procs.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']))\n"
        "        made += 1\n"
        "    except OSError:\n"
        "        break\n"
        "print('made', made)\n", encoding="utf-8")
    result = _tools(tmp_path, max_processes=3).run_command("python", [str(script)])
    made = int(result["stdout"].split()[-1])
    assert made <= 2  # the parent occupies one of the three slots


def test_memory_limit_is_enforced(tmp_path: Path) -> None:
    script = tmp_path / "hog.py"
    script.write_text(
        "try:\n"
        "    block = bytearray(1024 * 1024 * 1024)\n"
        "    print('allocated')\n"
        "except MemoryError:\n"
        "    print('denied')\n", encoding="utf-8")
    result = _tools(tmp_path, max_memory_bytes=256 * 1024**2).run_command(
        "python", [str(script)])
    assert "allocated" not in result["stdout"]


def test_ordinary_command_still_works(tmp_path: Path) -> None:
    result = _tools(tmp_path).run_command("python", ["-c", "print(6 * 7)"])
    assert result["return_code"] == 0 and result["stdout"].strip() == "42"

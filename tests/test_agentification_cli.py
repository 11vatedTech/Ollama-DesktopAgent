"""The CLI drives the full lifecycle: qualify -> create -> activate -> run -> verify."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from support.scripted_ollama import ScriptedOllama

from ollama_workstation.cli import main


@pytest.fixture
def cli(tmp_path: Path, monkeypatch, scripted: ScriptedOllama):
    monkeypatch.setenv("OLLAMA_WORKSTATION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OLLAMA_WORKSTATION_OLLAMA_URL", scripted.base_url)

    def run(*argv: str) -> int:
        return main([str(item) for item in argv])

    return run


def test_full_agent_lifecycle_from_the_command_line(cli, tmp_path: Path, capsys) -> None:
    model = "json-only:latest"
    assert cli("qualify", model) == 2  # no profile for the installed digest yet
    assert "11vated profile" in capsys.readouterr().err
    assert cli("profile", model, "--metadata-only") == 0
    capsys.readouterr()
    assert cli("agent", "create", "Clerk", "--model", model) == 0
    assert "no harness qualification" in capsys.readouterr().out
    assert cli("agent", "activate", "Clerk") == 2
    assert "no harness qualification" in capsys.readouterr().err

    assert cli("qualify", model, "--trials", "1") == 0
    out = capsys.readouterr().out
    assert "QUALIFIED" in out and "harness: structured_actions" in out
    assert "native_tools" in out and "unsupported" in out

    assert cli("agent", "activate", "Clerk") == 0
    capsys.readouterr()
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps([
        {"kind": "run_completed"},
        {"kind": "file_contains", "path": "status.txt", "text": "ready-XYZ"},
    ]), encoding="utf-8")
    workspace = tmp_path / "ws"
    task = "Create a file named status.txt containing the text ready-XYZ. Then report it."
    assert cli("agent", "run", "Clerk", workspace, task, "--checks", checks, "--json") == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["verified"] and outcome["verification"]["status"] == "passed"
    assert (workspace / "status.txt").read_text(encoding="utf-8") == "ready-XYZ"

    checks.write_text(json.dumps([{"kind": "file_exists", "path": "never-created.txt"}]),
                      encoding="utf-8")
    assert cli("agent", "run", "Clerk", workspace, task, "--checks", checks, "--yes") == 1
    assert "verification=failed" in capsys.readouterr().err
    assert cli("verify", workspace, checks) == 1
    assert json.loads(capsys.readouterr().out)["failure_reasons"] == ["artifact_missing"]

    assert cli("agent", "list") == 0
    assert "Clerk" in capsys.readouterr().out
    assert cli("agent", "show", "Clerk") == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["state"]["status"] == "active" and shown["readiness_issues"] == []
    assert cli("agent", "retire", "Clerk") == 0
    assert cli("agent", "run", "Clerk", workspace, task) == 2
    assert cli("agent", "show", "nobody") == 2


def test_skill_and_acceptance_commands(cli, tmp_path: Path, capsys) -> None:
    for model in ("careless:latest", "native-pro:latest"):
        assert cli("profile", model, "--metadata-only") == 0
    assert cli("skill", "list") == 0
    assert "inspect-before-edit" in capsys.readouterr().out
    assert cli("skill", "evaluate", "inspect-before-edit", "--model", "careless:latest") == 2
    assert cli("qualify", "careless:latest", "--trials", "1") == 0
    capsys.readouterr()
    assert cli("skill", "evaluate", "inspect-before-edit", "--model", "careless:latest",
               "--trials", "1") == 0
    assert "IMPROVED (accepted)" in capsys.readouterr().out
    assert cli("skill", "evaluate", "defect-fix-loop", "--model", "careless:latest") == 2
    assert "--allow-code-execution" in capsys.readouterr().err

    report = tmp_path / "acceptance.json"
    code = cli("accept", "--models", "native-pro:latest", "careless:latest", "--scenarios",
               "C,F,A,B", "--trials", "1", "--output", report)
    assert code == 1  # A is blocked without consent to execute model-written code
    statuses = {item["scenario"]: item["status"]
                for item in json.loads(report.read_text(encoding="utf-8"))["results"]}
    assert statuses == {"C": "passed", "F": "inconclusive", "A": "blocked", "B": "passed"}
    assert cli("accept", "--models", "native-pro:latest", "--scenarios", "Z") == 2

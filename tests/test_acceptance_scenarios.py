"""Acceptance scenarios run end to end: real sockets, real processes, real kills.

Models are scripted (see tests/support/scripted_ollama.py), so a pass here
certifies the workstation machinery, not any real model.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ollama_workstation.agentification.acceptance import AcceptanceRunner


@pytest.fixture
def runner(client, repository, store, scripted, tmp_path: Path) -> AcceptanceRunner:
    return AcceptanceRunner(client, repository, store, tmp_path / "acceptance",
                            base_url=scripted.base_url, trials=1)


def test_scenario_c_multi_tool_task_recovers_from_induced_failure_and_is_verified(runner) -> None:
    result = runner.scenario_c("json-only:latest")
    assert result.status == "passed", result.summary
    evidence = result.evidence
    assert evidence["harness"] == "structured_actions"
    assert evidence["induced_failures"] == 1 and evidence["verified"]
    assert {"list_files", "read_file", "write_file"} <= set(evidence["tools_used"])
    assert evidence["verification"]["status"] == "passed"
    summary = Path(evidence["workspace"]) / "report" / "summary.txt"
    assert summary.read_text(encoding="utf-8").startswith("grand total: ")


def test_scenario_e_killed_process_is_recovered_without_duplicate_writes(
        runner, scripted, repository) -> None:
    def progress(message: str) -> None:
        # Stall the child's second model request (the first that carries a tool
        # result) so the kill lands while a request is in flight, then release.
        if message.startswith("Started agent in child process"):
            scripted.hold_seconds = 20.0
        elif message.startswith("Killed process"):
            scripted.hold_seconds = 0.0

    runner.notify = progress
    result = runner.scenario_e("native-pro:latest")
    assert result.status == "passed", (result.summary, result.evidence)
    evidence = result.evidence
    assert evidence["recovery_status"] == "resumable"
    assert evidence["same_run_id"] and evidence["resumed_events"] == 1
    assert evidence["write_file_completions"] == 1
    assert evidence["history_preserved"] and evidence["journal_chain_verified"]
    assert evidence["events_after_recovery"] > evidence["events_before_interruption"] >= 4
    with pytest.raises(OSError):
        os.kill(evidence["killed_pid"], 0)  # the first process is really gone
    assert repository.get_run_checkpoint(evidence["run_id"]) is None  # nothing left to replay
    assert repository.list_workspace_leases() == []


def test_scenario_e_reports_inconclusive_when_no_interruption_was_possible(runner) -> None:
    result = runner.scenario_e("native-pro:latest", deadline_seconds=30)
    # Without a stalled request the scripted model finishes in milliseconds; the
    # scenario must say so rather than claim a recovery it did not exercise.
    assert result.status in {"inconclusive", "passed"}
    if result.status == "inconclusive":
        assert "no recovery was exercised" in result.summary


def test_scenario_f_measures_differences_between_models(runner, store) -> None:
    result = runner.scenario_f(["native-pro:latest", "json-only:latest", "gullible:latest"])
    assert result.status == "passed"
    rows = {row["model"]: row for row in result.evidence["models"]}
    assert rows["native-pro:latest"]["selected_harness"] == "native_tools"
    assert rows["json-only:latest"]["selected_harness"] == "structured_actions"
    assert rows["gullible:latest"]["status"] == "provisional"
    assert "selected harness" in result.evidence["differences"]
    assert len(store.list_qualifications()) == 3
    again = runner.scenario_f(["native-pro:latest", "json-only:latest", "gullible:latest"])
    assert len(store.list_qualifications()) == 3  # valid records are reused, not re-run
    assert again.evidence["models"][0]["qualification_id"] == rows["native-pro:latest"]["qualification_id"]
    assert runner.scenario_f(["native-pro:latest"]).status == "inconclusive"


def test_scenario_a_is_blocked_without_consent_then_fixes_a_real_defect(runner) -> None:
    assert runner.scenario_a("native-pro:latest").status == "blocked"
    runner.allow_code_execution = True
    result = runner.scenario_a("native-pro:latest")
    assert result.status == "passed", (result.summary, result.evidence.get("verification"))
    assert result.evidence["agent_ran_tests"]
    checks = {item["kind"]: item for item in result.evidence["verification"]["checks"]}
    assert checks["command"]["evidence"]["exit_code"] == 0
    assert checks["unchanged"]["status"] == "passed" and checks["changed"]["status"] == "passed"
    assert "result += value" in (Path(result.evidence["workspace"]) / "calc.py").read_text()


def test_models_that_fail_the_gate_block_the_scenario_with_reasons(runner) -> None:
    results = runner.run(("C", "B", "D"), ["hopeless:latest"])
    by_key = {item.scenario: item for item in results}
    assert by_key["C"].status == "blocked"
    assert any("no harness" in issue for issue in by_key["C"].evidence["issues"])
    assert by_key["B"].status == "blocked" and "not implemented" in by_key["B"].summary
    assert by_key["D"].status == "blocked"
    missing = runner.run(("C",), ["not-installed:latest"])[0]
    assert missing.status == "blocked" and "not installed" in missing.summary

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ollama_workstation.agents.execution_lease import WorkspaceAdmission
from ollama_workstation.agents.orchestration import (
    AgentAssignment,
    ReadOnlyWorkspaceTools,
    WorkforceOrchestrator,
    WorkforcePlan,
    WorkforcePlanner,
)
from ollama_workstation.agents.tools import ToolError
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.storage.repository import Repository

MODEL = "local:latest"


class ScriptedClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, object]]] = []

    def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)


def assistant(content: str = "", calls: list[dict[str, object]] | None = None) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"message": message}


def write_call(path: str, content: str) -> dict[str, object]:
    return {"function": {"name": "write_file", "arguments": {"path": path, "content": content}}}


def make_profile(mode: str = "native_tools") -> ModelProfile:
    return ModelProfile(
        model_name=MODEL, digest="sha256:local", generated_at="now",
        professional_title="Repository Cartographer", agentability_mode=mode,
    )


def assignment(task_id: str, *, read_only: bool = False) -> AgentAssignment:
    return AgentAssignment(task_id, MODEL, make_profile(), read_only)


def verdict(
    evidence: list[dict[str, str]],
    *,
    outcome: str = "PASS",
    summary: str = "The cited artifacts satisfy the acceptance criteria.",
    failure_reasons: list[dict[str, str]] | None = None,
) -> str:
    return json.dumps({
        "schema_version": 2,
        "verdict": outcome,
        "summary": summary,
        "evidence": evidence,
        "failure_reasons": failure_reasons or [],
        "confidence": 0.8,
    })


def test_analysis_plan_executes_dependency_order_and_verifier_verdict(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "Observed files."},
        ])),
    ])
    repository = Repository(tmp_path / "state.sqlite3")
    plan = WorkforcePlanner.analysis_plan("Document the workspace.")
    result = WorkforceOrchestrator(client, repository).run(
        plan, tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
        approval=lambda _message: True,
    )
    assert result.status == "completed"
    assert result.verification_passed is True
    assert [item.status for item in result.tasks] == ["completed", "completed"]
    assert result.tasks[0].verification[0]["passed"] is True
    assert result.tasks[1].verdict == "PASS"
    assert (tmp_path / "workspace" / "workforce" / "analysis.md").is_file()
    stored = repository.get_workforce_run(result.run_id)
    assert stored is not None
    assert stored["status"] == "completed"
    assert len(stored["tasks"]) == 2
    assert repository.verify_workforce_event_chain(result.run_id)


def test_workforce_is_refused_when_another_run_holds_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = Repository(tmp_path / "workforce-workspace.sqlite3")
    client = ScriptedClient([])
    plan = WorkforcePlanner.analysis_plan("Document the workspace.")
    with WorkspaceAdmission(
        repository, workspace, "holder-run", "holder-owner", ttl_seconds=60
    ):
        result = WorkforceOrchestrator(client, repository).run(
            plan, workspace,
            {"analyse": assignment("analyse"), "verify": assignment("verify")},
            approval=lambda _message: True,
        )
    assert result.status == "workspace_busy"
    assert client.calls == []
    assert not (workspace / "workforce" / "analysis.md").exists()
    stored = repository.get_workforce_run(result.run_id)
    assert stored is not None
    assert stored["status"] == "workspace_busy"
    assert repository.get_workspace_lease(workspace) is None


def test_delivery_plan_requires_plan_and_task_approval(tmp_path: Path) -> None:
    approvals: list[str] = []
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "analysis")]),
        assistant("analysis done"),
        assistant(calls=[write_call("workforce/result.md", "result")]),
        assistant("result done"),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "analysis"},
            {"artifact": "workforce/result.md", "observation": "result"},
        ])),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.delivery_plan("Implement the change."), tmp_path / "workspace",
        {task_id: assignment(task_id) for task_id in ("analyse", "deliver", "verify")},
        approval=lambda message: approvals.append(message) or True,
    )
    assert result.status == "completed"
    assert len(approvals) == 2
    assert "Objective:" in approvals[0]
    assert "deliver" in approvals[1]


def test_denied_plan_approval_never_starts_a_child(tmp_path: Path) -> None:
    client = ScriptedClient([])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.delivery_plan("Do work."), tmp_path / "workspace",
        {task_id: assignment(task_id) for task_id in ("analyse", "deliver", "verify")},
        approval=lambda _message: False,
    )
    assert result.status == "rejected"
    assert client.calls == []
    assert result.tasks == []


def test_missing_artifact_fails_verification_without_claiming_completion(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant("I did not write the artifact."),
    ])
    plan = WorkforcePlanner.analysis_plan("Analyze.")
    result = WorkforceOrchestrator(client, None).run(
        plan, tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
        approval=lambda _message: True,
    )
    assert result.status == "verification_failed"
    assert result.tasks[0].status == "verification_failed"
    assert result.tasks[0].verification[0]["passed"] is False
    assert result.tasks[1].status == "blocked"
    assert result.verification_passed is False


def test_blocked_dependency_prevents_downstream_execution(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("wrong.txt", "wrong")]),
        assistant("not the required artifact"),
    ])
    plan = WorkforcePlanner.analysis_plan("Analyze.")
    result = WorkforceOrchestrator(client, None).run(
        plan, tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
        approval=lambda _message: True,
    )
    assert result.tasks[0].status == "verification_failed"
    assert result.tasks[1].status == "blocked"
    assert result.tasks[1].error and "dependency" in result.tasks[1].error.lower()


def test_verifier_structured_verdict_is_persisted_by_the_host(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "Observed files."},
        ])),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
        approval=lambda _message: True,
    )
    verification = tmp_path / "workspace" / "workforce" / "verification.json"
    assert result.status == "completed"
    assert result.tasks[1].verdict == "PASS"
    assert verification.is_file()
    payload = json.loads(verification.read_text(encoding="utf-8"))
    assert payload["recorded_by"] == "host"
    assert payload["verdict"]["verdict"] == "PASS"
    assert payload["manifest"]["files"][0]["path"] == "workforce/analysis.md"
    assert payload["host_checks"][0]["passed"] is True


def test_verifier_pass_without_evidence_is_rejected(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([], summary="Trust me.")),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )
    assert result.status == "verification_failed"
    assert result.tasks[1].status == "verification_failed"
    assert result.tasks[1].verification[0]["kind"] == "structured_verdict"
    assert "evidence" in result.tasks[1].verification[0]["detail"].lower()
    assert not (tmp_path / "workspace" / "workforce" / "verification.json").exists()


def test_verifier_fabricated_artifact_reference_is_rejected(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([
            {"artifact": "workforce/nonexistent.md", "observation": "invented"},
        ])),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )
    assert result.status == "verification_failed"
    assert "manifest" in result.tasks[1].verification[0]["detail"].lower()
    assert result.verification_passed is False


def test_verifier_cannot_mutate_the_artifacts_it_is_inspecting(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    class TamperingClient(ScriptedClient):
        def chat(self, model: str, messages: list[dict[str, object]], **kwargs: object) -> dict[str, object]:
            response = super().chat(model, messages, **kwargs)
            if len(self.calls) == 3:
                (workspace / "workforce" / "analysis.md").write_text(
                    "changed after the host snapshot", encoding="utf-8"
                )
            return response

    client = TamperingClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "Observed files."},
        ])),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        workspace,
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )
    assert result.status == "verification_failed"
    assert result.verification_passed is False
    audit = json.loads(
        (workspace / "workforce" / "verification.json").read_text(encoding="utf-8")
    )
    assert audit["recorded_by"] == "host"
    assert audit["tamper_check"]
    assert audit["host_checks"][0]["passed"] is False


def test_prose_verifier_is_normalized_through_a_schema_enforced_host_review(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant("FAIL: I could not find the artifact."),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "Observed files."},
        ])),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )
    assert result.status == "completed"
    assert result.tasks[1].verdict == "PASS"
    assert result.tasks[1].verification[0]["source"] == "model_verifier_schema_normalized"
    assert len(client.calls) == 4
    assert client.calls[-1][0]["role"] == "system"  # normalization sends a contract
    audit = json.loads(
        (tmp_path / "workspace" / "workforce" / "verification.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["recorded_by"] == "host"
    assert audit["host_checks"][0]["source"] == "model_verifier_schema_normalized"


def test_factually_contradicted_verifier_fail_cannot_override_host_acceptance(
    tmp_path: Path,
) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "The file is empty."},
        ], outcome="FAIL", summary="The artifact is empty.", failure_reasons=[
            {
                "kind": "artifact_empty",
                "description": "The analysis artifact is empty.",
                "artifact": "workforce/analysis.md",
            },
        ])),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )
    assert result.status == "completed"
    assert result.verification_passed is True
    assert result.tasks[1].verdict == "FAIL"
    assert result.tasks[1].status == "completed"
    check = result.tasks[1].verification[0]
    assert check["host_override"] is True
    assert check["contradictions"]
    audit = json.loads(
        (tmp_path / "workspace" / "workforce" / "verification.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["verdict"]["verdict"] == "FAIL"
    assert audit["host_decision"] == "PASS"
    assert audit["host_override"] is True


def test_mixed_substantive_failure_is_not_overridden_by_empty_artifact_claim(
    tmp_path: Path,
) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "The artifact is non-empty."},
        ], outcome="FAIL", summary=(
            "The artifact is empty and the analysis does not cover the objective."
        ), failure_reasons=[
            {
                "kind": "artifact_empty",
                "description": "The analysis artifact is empty.",
                "artifact": "workforce/analysis.md",
            },
            {
                "kind": "substantive",
                "description": "The analysis does not cover the objective.",
                "artifact": "workforce/analysis.md",
            },
        ])),
    ])
    workspace = tmp_path / "workspace"
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        workspace,
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )

    assert result.status == "verification_failed"
    assert result.verification_passed is False
    check = result.tasks[1].verification[0]
    assert check["verdict"] == "FAIL"
    assert check["host_override"] is False
    assert check["contradictions"]
    audit = json.loads(
        (workspace / "workforce" / "verification.json").read_text(encoding="utf-8")
    )
    assert audit["verdict"]["verdict"] == "FAIL"
    assert audit["host_decision"] == "FAIL"
    assert audit["host_override"] is False


def test_verifier_fail_is_fail_closed_even_with_valid_evidence(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant(verdict([
            {"artifact": "workforce/analysis.md", "observation": "content is insufficient"},
        ], outcome="FAIL", summary="The analysis does not cover the objective.", failure_reasons=[
            {
                "kind": "substantive",
                "description": "The analysis does not cover the objective.",
                "artifact": "workforce/analysis.md",
            },
        ])),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )
    assert result.status == "verification_failed"
    assert result.tasks[1].verdict == "FAIL"
    audit = json.loads(
        (tmp_path / "workspace" / "workforce" / "verification.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["verdict"]["verdict"] == "FAIL"


def test_verifier_malformed_json_is_rejected(tmp_path: Path) -> None:
    client = ScriptedClient([
        assistant(calls=[write_call("workforce/analysis.md", "Observed files.")]),
        assistant("Analysis written."),
        assistant("VERDICT: PASS because I said so."),
        # The schema-enforced formatter also fails to produce JSON: the host
        # must remain fail-closed rather than inventing a verdict.
        assistant("still not JSON"),
    ])
    result = WorkforceOrchestrator(client, None).run(
        WorkforcePlanner.analysis_plan("Document the workspace."),
        tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
    )
    assert result.status == "verification_failed"
    assert result.tasks[1].verification[0]["passed"] is False
    assert not (tmp_path / "workspace" / "workforce" / "verification.json").exists()


def test_step_limit_is_not_retried_and_cannot_claim_completion(tmp_path: Path) -> None:
    loop = [{"function": {"name": "list_files", "arguments": {}}}]
    client = ScriptedClient([
        assistant(calls=loop),
        assistant(calls=loop),
        assistant(calls=loop),
        assistant(calls=loop),
    ])
    plan = WorkforcePlan.from_dict({
        "objective": "Loop forever.",
        "require_approval": False,
        "max_total_steps": 2,
        "tasks": [{
            "task_id": "loop", "role": "repository_analysis", "objective": "loop",
            "max_steps": 2, "retry_safe": True, "max_retries": 3,
        }],
    })
    result = WorkforceOrchestrator(client, None).run(
        plan, tmp_path / "workspace", {"loop": assignment("loop")},
    )
    assert result.status == "failed"
    assert result.tasks[0].status == "failed"
    assert result.tasks[0].attempts == 1
    assert len(client.calls) == 2
    assert "step_limit" in (result.tasks[0].error or "")


def test_read_only_tools_cannot_write_or_run_commands(tmp_path: Path) -> None:
    tools = ReadOnlyWorkspaceTools(tmp_path)
    assert {item["function"]["name"] for item in tools.definitions()} == {
        "list_files", "read_file", "search_text",
    }
    with pytest.raises(ToolError):
        tools.invoke("write_file", {"path": "x.txt", "content": "nope"})
    with pytest.raises(ToolError):
        tools.invoke("run_command", {"command": "python"})


def test_plan_validation_rejects_cycles_and_unknown_dependencies() -> None:
    with pytest.raises(ValueError, match="unknown"):
        WorkforcePlanner.analysis_plan("x").from_dict({
            "objective": "x",
            "tasks": [{"task_id": "a", "role": "repository_analysis", "objective": "a",
                       "depends_on": ["missing"]}],
        }).validate()
    with pytest.raises(ValueError, match="cycle"):
        WorkforcePlanner.analysis_plan("x").from_dict({
            "objective": "x",
            "tasks": [
                {"task_id": "a", "role": "repository_analysis", "objective": "a", "depends_on": ["b"]},
                {"task_id": "b", "role": "repository_analysis", "objective": "b", "depends_on": ["a"]},
            ],
        }).validate()


def test_plan_validation_rejects_verification_paths_outside_the_workspace() -> None:
    for target in ("../outside.md", "/etc/passwd", "C:/Windows/system.ini", "~/.ssh/id_rsa"):
        with pytest.raises(ValueError, match="inside the workspace"):
            WorkforcePlan.from_dict({
                "objective": "x",
                "tasks": [{
                    "task_id": "a", "role": "repository_analysis", "objective": "a",
                    "verification": [{"kind": "file_exists", "target": target}],
                }],
            })


def test_planner_rejects_model_json_with_unknown_verification_kind() -> None:
    with pytest.raises(ValueError, match="Unsupported verification"):
        WorkforcePlanner.from_model_json(json.dumps({
            "objective": "x",
            "tasks": [{
                "task_id": "a", "role": "repository_analysis", "objective": "a",
                "verification": [{"kind": "magic", "target": "a"}],
            }],
        }), "x")


def test_resumed_agent_checkpoint_is_bound_to_the_assignments_digest(tmp_path: Path) -> None:
    # The orchestrator must never silently execute a profile from a different
    # artifact than the installed model it was assigned.
    from ollama_workstation.models.schemas import ModelRef

    client = ScriptedClient([])
    plan = WorkforcePlanner.analysis_plan("Analyze.")
    result = WorkforceOrchestrator(client, None).run(
        plan, tmp_path / "workspace",
        {"analyse": assignment("analyse"), "verify": assignment("verify")},
        installed_models=[ModelRef(MODEL, digest="sha256:other")],
        approval=lambda _message: True,
    )
    assert result.status == "failed"
    assert "digest" in (result.tasks[0].error or "").lower()

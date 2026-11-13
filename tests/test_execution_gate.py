"""The shared execution gate used by every legacy agent entry point."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_profile

from ollama_workstation.agentification.gate import (
    ExecutionRefused,
    clear_for_execution,
    clear_repository_run,
)
from ollama_workstation.agentification.harness import HarnessLab
from ollama_workstation.agentification.resources import (
    AdmissionDecision,
    ResourceDenied,
)
from ollama_workstation.agents.orchestration import ReadOnlyWorkspaceTools, WorkforceOrchestrator
from ollama_workstation.agents.tools import WorkspaceTools


def _defs(tmp_path: Path, *, read_only: bool = False) -> list[dict]:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    tools = ReadOnlyWorkspaceTools(workspace) if read_only else WorkspaceTools(workspace)
    return tools.definitions()


class _Admission:
    def __init__(self, outcome: str = "admit") -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, int | None]] = []

    def require(self, model: str, num_ctx: int | None = None) -> AdmissionDecision:
        self.calls.append((model, num_ctx))
        decision = AdmissionDecision(model, self.outcome, (f"{self.outcome} by test",),
                                     1, None, 0, 0)
        if not decision.admitted:
            raise ResourceDenied(decision)
        return decision


def test_unqualified_model_is_refused_with_the_command_to_fix_it(
        client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    with pytest.raises(ExecutionRefused) as error:
        clear_for_execution(client, store, model_name="native-pro:latest",
                            model_digest=profile.digest, tool_definitions=_defs(tmp_path))
    assert any("11vated qualify native-pro:latest" in issue for issue in error.value.issues)


def test_operator_override_is_explicit_and_recorded(client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    clearance = clear_for_execution(
        client, store, model_name="native-pro:latest", model_digest=profile.digest,
        tool_definitions=_defs(tmp_path), allow_unqualified=True)
    assert clearance.override and clearance.harness is None
    assert clearance.qualification_status == "unqualified"
    assert "UNQUALIFIED" in clearance.summary()


def test_qualified_model_gets_the_measured_harness_and_admission(
        client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "json-only:latest")
    record = HarnessLab(client).qualify("json-only:latest", profile, trials=1, seed=3)
    store.save_qualification(record.to_dict())
    admission = _Admission()
    clearance = clear_for_execution(
        client, store, model_name="json-only:latest", model_digest=profile.digest,
        tool_definitions=_defs(tmp_path), admission=admission)
    assert clearance.harness == "structured_actions" == record.selected_harness
    assert clearance.qualification_id == record.qualification_id
    assert admission.calls == [("json-only:latest", None)]
    assert clearance.admission["outcome"] == "admit"


def test_read_only_subset_is_covered_but_shell_superset_is_not(
        client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    store.save_qualification(
        HarnessLab(client).qualify("native-pro:latest", profile, trials=1, seed=3).to_dict())
    subset = clear_for_execution(client, store, model_name="native-pro:latest",
                                 model_digest=profile.digest,
                                 tool_definitions=_defs(tmp_path, read_only=True))
    assert subset.harness is not None
    shell = [*_defs(tmp_path), {"type": "function", "function": {"name": "run_command"}}]
    with pytest.raises(ExecutionRefused, match="does not cover: run_command"):
        clear_for_execution(client, store, model_name="native-pro:latest",
                            model_digest=profile.digest, tool_definitions=shell)


def test_extra_mcp_tools_are_flagged_not_hidden(client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    store.save_qualification(
        HarnessLab(client).qualify("native-pro:latest", profile, trials=1, seed=3).to_dict())
    extra = [*_defs(tmp_path), {"type": "function", "function": {"name": "mcp__docs__search"}}]
    clearance = clear_for_execution(client, store, model_name="native-pro:latest",
                                    model_digest=profile.digest, tool_definitions=extra)
    assert clearance.unqualified_tools == ("mcp__docs__search",)
    assert any("outside qualified evidence" in item for item in clearance.warnings)


def test_hopeless_model_is_refused_even_after_qualification(
        client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "hopeless:latest")
    store.save_qualification(
        HarnessLab(client).qualify("hopeless:latest", profile, trials=1, seed=3).to_dict())
    with pytest.raises(ExecutionRefused, match="no harness"):
        clear_for_execution(client, store, model_name="hopeless:latest",
                            model_digest=profile.digest, tool_definitions=_defs(tmp_path))


def test_digest_drift_and_missing_model_are_refused(client, repository, store, tmp_path) -> None:
    with pytest.raises(ExecutionRefused, match="not installed"):
        clear_for_execution(client, store, model_name="absent:latest", model_digest="x",
                            tool_definitions=_defs(tmp_path))
    with pytest.raises(ExecutionRefused, match="digest differs"):
        clear_for_execution(client, store, model_name="native-pro:latest",
                            model_digest="0" * 64, tool_definitions=_defs(tmp_path))


def test_admission_denial_propagates(client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    store.save_qualification(
        HarnessLab(client).qualify("native-pro:latest", profile, trials=1, seed=3).to_dict())
    with pytest.raises(ResourceDenied):
        clear_for_execution(client, store, model_name="native-pro:latest",
                            model_digest=profile.digest, tool_definitions=_defs(tmp_path),
                            admission=_Admission("deny"))


def test_resume_keeps_the_checkpoint_harness(client, repository, store, tmp_path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    store.save_qualification(
        HarnessLab(client).qualify("native-pro:latest", profile, trials=1, seed=3).to_dict())
    _clearance, policy = clear_repository_run(
        client, repository, model_name="native-pro:latest", model_digest=profile.digest,
        tool_definitions=_defs(tmp_path), use_admission=False,
        resume_checkpoint={"harness_mode": "guided"})
    assert policy.harness_mode == "guided"


def test_workforce_child_refused_by_gate_fails_the_task_not_the_supervisor(
        tmp_path) -> None:
    from ollama_workstation.agents.orchestration import AgentAssignment, WorkforcePlanner
    from ollama_workstation.models.schemas import ModelProfile

    class NoCalls:
        def chat(self, *_args, **_kwargs):
            raise AssertionError("a refused child must never reach the provider")

    profile = ModelProfile(model_name="local:latest", digest="sha256:local", generated_at="now",
                           professional_title="Tester", agentability_mode="native_tools")
    calls: list[tuple[str, str]] = []

    def gate(model: str, digest: str, definitions: list[dict]) -> None:
        calls.append((model, digest))
        raise ExecutionRefused(["no harness qualification exists for this model digest"])

    plan = WorkforcePlanner.analysis_plan("Document the workspace.")
    result = WorkforceOrchestrator(NoCalls(), None, execution_gate=gate).run(
        plan, tmp_path / "workspace",
        {"analyse": AgentAssignment("analyse", "local:latest", profile),
         "verify": AgentAssignment("verify", "local:latest", profile)},
        approval=lambda _message: True,
    )
    assert calls[0] == ("local:latest", "sha256:local")
    assert result.status != "completed"
    assert result.tasks[0].status != "completed"

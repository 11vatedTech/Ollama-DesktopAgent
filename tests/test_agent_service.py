"""Agent definitions: versioning, the readiness gate, permissions and verified runs."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from conftest import make_profile
from support.scripted_ollama import ScriptedOllama

from ollama_workstation.agentification.agents import (
    AgentConfigError,
    AgentDefinition,
    AgentNotReady,
    Budget,
    Permissions,
)
from ollama_workstation.agentification.harness import HarnessLab
from ollama_workstation.agentification.store import StoreIntegrityError

CHECKS = [{"kind": "run_completed"},
          {"kind": "file_contains", "path": "status.txt", "text": "ready-ABC"},
          {"kind": "changes_within", "allowed": ["status.txt"]}]
TASK = "Create a file named status.txt containing the text ready-ABC. Then report it."


def _qualified(service, client, repository, store, model="native-pro:latest", **options):
    profile = make_profile(client, repository, model)
    record = HarnessLab(client).qualify(model, profile, trials=1, seed=3)
    store.save_qualification(record.to_dict())
    return profile, service.create(name=options.pop("name", "Builder"), profile=profile, **options)


def test_unqualified_agent_cannot_be_activated_or_run(service, client, repository, tmp_path) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    agent = service.create(name="Draft", profile=profile)
    assert service.state("Draft")["status"] == "draft"
    with pytest.raises(AgentNotReady) as error:
        service.activate("Draft")
    assert any("no harness qualification" in issue for issue in error.value.issues)
    with pytest.raises(AgentNotReady, match="activate it first"):
        service.run(agent.agent_id, TASK, tmp_path / "ws", profile)


def test_qualified_agent_runs_with_selected_harness_and_is_verified(
        service, client, repository, store, tmp_path) -> None:
    profile, agent = _qualified(service, client, repository, store, "json-only:latest")
    service.activate(agent.agent_id)
    outcome = service.run(agent.agent_id, TASK, tmp_path / "ws", profile, checks=CHECKS)
    assert outcome.result.status == "completed" and outcome.verified
    assert outcome.result.trace[0].data["mode"] == "structured_actions"
    binding = store.get_run_binding(outcome.result.run_id)
    assert binding["agent_id"] == agent.agent_id and binding["version"] == 1
    assert binding["config_sha256"] == agent.config_sha256
    assert binding["qualification_id"] == outcome.qualification_id
    stored = store.list_verifications(outcome.result.run_id)
    assert stored[0]["status"] == "passed" and stored[0]["run_id"] == outcome.result.run_id
    assert repository.list_runs()[0]["agent_title"].startswith("Builder (")


def test_completed_run_with_wrong_result_is_reported_failed(
        service, client, repository, store, tmp_path) -> None:
    profile, agent = _qualified(service, client, repository, store)
    service.activate(agent.agent_id)
    wrong = [{"kind": "file_contains", "path": "status.txt", "text": "ready-OTHER"}]
    outcome = service.run(agent.agent_id, TASK, tmp_path / "ws", profile, checks=wrong)
    assert outcome.result.status == "completed"  # the agent believes it succeeded
    assert not outcome.verified and outcome.report.failure_reasons == ("content_mismatch",)


def test_required_verification_without_checks_needs_human_review(
        service, client, repository, store, tmp_path) -> None:
    profile, agent = _qualified(service, client, repository, store,
                                verification={"require_checks": True})
    service.activate(agent.agent_id)
    outcome = service.run(agent.agent_id, TASK, tmp_path / "ws", profile)
    assert outcome.report.status.value == "needs_human_review" and not outcome.verified


def test_editing_creates_a_version_and_leaves_the_active_one_untouched(
        service, client, repository, store) -> None:
    _, agent = _qualified(service, client, repository, store)
    service.activate(agent.agent_id)
    updated = service.update("Builder", instructions="Be terse.", budget=Budget(max_steps=5))
    assert (updated.version, updated.parent_version) == (2, 1)
    assert service.state("Builder")["active_version"] == 1
    assert service.get("Builder", 1).instructions == ""
    assert store.agent_versions(agent.agent_id) == [1, 2]
    assert updated.config_sha256 != agent.config_sha256
    service.activate("Builder")
    assert service.state("Builder")["active_version"] == 2
    with pytest.raises(sqlite3.IntegrityError):
        store.save_agent_version(updated.to_dict())  # versions are immutable


def test_readiness_is_rechecked_at_run_time_when_the_model_or_runtime_changes(
        service, client, repository, store, tmp_path, scripted: ScriptedOllama) -> None:
    profile, agent = _qualified(service, client, repository, store)
    service.activate(agent.agent_id)
    scripted.version = "1.0.0"
    with pytest.raises(AgentNotReady) as error:
        service.run(agent.agent_id, TASK, tmp_path / "ws", profile)
    assert any("Ollama changed from 0.99.0 to 1.0.0" in issue for issue in error.value.issues)
    scripted.version = "0.99.0"
    del scripted.personas["native-pro:latest"]
    with pytest.raises(AgentNotReady, match="not installed"):
        service.run(agent.agent_id, TASK, tmp_path / "ws", profile)
    assert not (tmp_path / "ws" / "status.txt").exists()


def test_digest_change_blocks_the_agent(service, client, repository, store, monkeypatch) -> None:
    _, agent = _qualified(service, client, repository, store)
    monkeypatch.setattr(ScriptedOllama, "tag", staticmethod(
        lambda name: {"name": name, "digest": "f" * 64, "details": {}}))
    issues = service.readiness(agent)
    assert any("digest differs" in issue for issue in issues)


def test_provisional_qualification_requires_an_explicit_recorded_override(
        service, client, repository, store) -> None:
    _, agent = _qualified(service, client, repository, store, "gullible:latest")
    with pytest.raises(AgentNotReady, match="provisionally qualified"):
        service.activate(agent.agent_id)
    accepted = service.update(agent.agent_id, allow_provisional=True)
    assert service.activate(agent.agent_id).version == accepted.version
    assert service.get(agent.agent_id).to_dict()["allow_provisional"] is True


def test_changing_the_tool_set_requires_requalification(service, client, repository, store) -> None:
    profile, agent = _qualified(service, client, repository, store)
    shell = service.update(agent.agent_id, permissions=Permissions(
        shell=True, allowed_commands=("{python}",)))
    assert any("no harness qualification" in issue and "--shell" in issue
               for issue in service.readiness(shell))
    record = service.qualify(agent.agent_id, profile, trials=1)
    assert "run_command" in record.tool_names
    assert service.readiness(shell) == []


def test_permissions_are_enforced_by_the_host_not_the_model(
        service, client, repository, store, tmp_path) -> None:
    profile, agent = _qualified(service, client, repository, store,
                                permissions=Permissions(write=False))
    service.activate(agent.agent_id)
    outcome = service.run(agent.agent_id, TASK, tmp_path / "ws", profile, checks=CHECKS)
    assert not (tmp_path / "ws" / "status.txt").exists()
    assert any(e.kind == "tool_denied" and "write permission" in e.message
               for e in outcome.result.trace)
    assert not outcome.verified
    tools = service.build_tools(replace(agent, permissions=Permissions(overwrite="deny")),
                                tmp_path / "ws2", approval=lambda _d: True)
    tools.invoke("write_file", {"path": "a.txt", "content": "1"})
    with pytest.raises(Exception, match="not approved"):
        tools.invoke("write_file", {"path": "a.txt", "content": "2"})
    with pytest.raises(Exception, match="shell permission"):
        tools.invoke("run_command", {"command": "python"})


def test_skill_requirements_are_part_of_the_gate(service, client, repository, store) -> None:
    profile, _ = _qualified(service, client, repository, store)
    needs_shell = service.create(name="Fixer", profile=profile,
                                 skills=({"skill_id": "defect-fix-loop", "version": 1},))
    issues = service.readiness(needs_shell)
    assert any("run_command" in issue for issue in issues)
    assert any("shell" in issue and "permissions" in issue for issue in issues)
    ghost = service.create(name="Ghost", profile=profile,
                           skills=({"skill_id": "does-not-exist", "version": 1},))
    assert any("Unknown skill" in issue for issue in service.readiness(ghost))
    editor = service.create(name="Editor", profile=profile,
                            skills=({"skill_id": "inspect-before-edit", "version": 1},))
    assert service.readiness(editor) == []
    service.skills.retire("inspect-before-edit")
    assert any("retired" in issue for issue in service.readiness(editor))


def test_roles_must_be_earned_or_explicitly_overridden(service, client, repository) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    with pytest.raises(AgentConfigError, match="not supported by this model's measured profile"):
        service.create(name="Fake Expert", profile=profile, role="software_engineering")
    agent = service.create(name="Override", profile=profile, role="software_engineering",
                           allow_unearned_role=True)
    assert agent.context["role_override"] is True
    assert service.create(name="Default", profile=profile).role == "general_assistant"


def test_lifecycle_duplicate_disable_retire(service, client, repository, store, tmp_path) -> None:
    profile, agent = _qualified(service, client, repository, store)
    service.activate(agent.agent_id)
    copy = service.duplicate("Builder", "Builder Two")
    assert copy.agent_id != agent.agent_id and copy.version == 1
    assert service.state("Builder Two")["status"] == "draft"
    with pytest.raises(AgentConfigError, match="already exists"):
        service.duplicate("Builder", "builder two")
    service.disable("Builder")
    with pytest.raises(AgentNotReady, match="disabled"):
        service.run(agent.agent_id, TASK, tmp_path / "ws", profile)
    service.retire("Builder")
    with pytest.raises(AgentNotReady, match="retired"):
        service.activate("Builder")
    with pytest.raises(AgentConfigError, match="retired"):
        service.update("Builder", instructions="x")
    with pytest.raises(KeyError):
        service.get("nobody")


@pytest.mark.parametrize("change", [
    {"name": "x"}, {"model_digest": ""}, {"role": "wizard"}, {"harness": "telepathy"},
    {"permissions": Permissions(shell=True)}, {"budget": Budget(max_steps=0)},
    {"budget": Budget(max_tool_calls=500)}, {"permissions": Permissions(overwrite="always")},
    {"skills": ({"skill_id": "unpinned"},)}, {"instructions": "x" * 9000},
])
def test_invalid_definitions_are_rejected(change: dict) -> None:
    base = AgentDefinition("id", 1, "Valid Name", "m:1", "digest", "general_assistant", "Title")
    with pytest.raises(AgentConfigError):
        AgentDefinition.from_dict(replace(base, **change).to_dict())


def test_tampered_definitions_are_detected(service, client, repository, store) -> None:
    profile = make_profile(client, repository, "native-pro:latest")
    agent = service.create(name="Honest", profile=profile)
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE agent_definitions SET config_json=replace(config_json, "
                   "'\"shell\": false', '\"shell\": true') WHERE agent_id=?", (agent.agent_id,))
    with pytest.raises(StoreIntegrityError):
        service.get(agent.agent_id)

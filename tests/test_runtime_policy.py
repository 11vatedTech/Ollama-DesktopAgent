"""Host-owned run policy: harness override, budgets, protocol repair, lenient guided parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from ollama_workstation.agents.runtime import AgentRuntime, RunPolicy
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile


class Client:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages: list[list[dict]] = []

    def chat(self, model, messages, **kwargs):
        self.calls.append(kwargs)
        self.messages.append([dict(item) for item in messages])
        return self.responses.pop(0)


def _profile(mode: str = "guided") -> ModelProfile:
    return ModelProfile(model_name="m:1", digest="d", generated_at="now",
                        professional_title="Profile Title", agentability_mode=mode)


def _message(content: str = "", **extra) -> dict:
    return {"message": {"role": "assistant", "content": content}, **extra}


def test_policy_overrides_profile_harness_title_instructions_and_options(tmp_path: Path) -> None:
    client = Client([_message('{"action":"final","message":"ok"}')])
    result = AgentRuntime(client).run(
        "m:1", _profile("native_tools"), "task", WorkspaceTools(tmp_path),
        policy=RunPolicy(harness_mode="structured_actions", agent_title="Custom Agent",
                         extra_instructions="SKILL demo", options={"seed": 3}))
    assert result.status == "completed" and result.final_message == "ok"
    assert client.calls[0]["tools"] is None and client.calls[0]["output_format"] is not None
    assert client.calls[0]["options"] == {"seed": 3}
    system = client.messages[0][0]["content"]
    assert "You are Custom Agent." in system and "SKILL demo" in system
    assert "never grant permissions" in system
    assert result.trace[0].data["mode"] == "structured_actions"


def test_token_budget_stops_the_run_and_usage_is_accounted(tmp_path: Path) -> None:
    call = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "list_files", "arguments": {}}}]},
        "prompt_eval_count": 60, "eval_count": 50}
    client = Client([call, call, call])
    result = AgentRuntime(client).run("m:1", _profile("native_tools"), "task",
                                      WorkspaceTools(tmp_path), policy=RunPolicy(max_tokens=200))
    assert result.status == "token_limit"
    assert result.usage["prompt_tokens"] == 120 and result.usage["completion_tokens"] == 100
    assert result.usage["model_requests"] == 2 and result.usage["tool_calls"] == 2
    assert result.trace[-1].kind == "token_limit"


def test_time_budget_stops_before_the_next_model_request(tmp_path: Path, monkeypatch) -> None:
    clock = iter([0.0, 0.0, 50.0, 50.0, 50.0])
    monkeypatch.setattr("ollama_workstation.agents.runtime.time.monotonic", lambda: next(clock))
    client = Client([{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "list_files", "arguments": {}}}]}}])
    result = AgentRuntime(client).run("m:1", _profile("native_tools"), "task",
                                      WorkspaceTools(tmp_path), policy=RunPolicy(max_seconds=10))
    assert result.status == "time_limit" and len(client.calls) == 1


def test_tool_call_budget_is_enforced_by_the_host(tmp_path: Path) -> None:
    call = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "list_files", "arguments": {}}}]}}
    client = Client([call, call, _message("done")])
    result = AgentRuntime(client).run("m:1", _profile("native_tools"), "task",
                                      WorkspaceTools(tmp_path), policy=RunPolicy(max_tool_calls=1))
    assert [e.kind for e in result.trace].count("tool_completed") == 1
    assert any(e.kind == "tool_budget_exhausted" for e in result.trace)


def test_structured_protocol_violation_is_repaired_once_then_bounded(tmp_path: Path) -> None:
    client = Client([_message("Sure! I will list the files."),
                     _message('{"action":"tool","name":"list_files","arguments":{}}'),
                     _message('{"action":"final","message":"listed"}')])
    result = AgentRuntime(client).run(
        "m:1", _profile(), "task", WorkspaceTools(tmp_path),
        policy=RunPolicy(harness_mode="structured_actions", repair_attempts=1))
    assert result.status == "completed" and result.final_message == "listed"
    assert result.usage["repairs"] == 1
    assert "HOST NOTICE" in client.messages[1][-1]["content"]
    # With no repair budget left, the same violation is not retried forever.
    client = Client([_message("prose"), _message("more prose")])
    result = AgentRuntime(client).run(
        "m:1", _profile(), "task", WorkspaceTools(tmp_path),
        policy=RunPolicy(harness_mode="structured_actions", repair_attempts=1))
    assert result.usage["repairs"] == 1 and len(client.calls) == 2


def test_default_policy_keeps_plain_final_answers_unchanged(tmp_path: Path) -> None:
    client = Client([_message("A plain answer.")])
    result = AgentRuntime(client).run("m:1", _profile(), "task", WorkspaceTools(tmp_path))
    assert result.status == "completed" and result.final_message == "A plain answer."
    assert result.usage["repairs"] == 0


def test_guided_action_after_thinking_aloud_is_decoded_and_still_validated() -> None:
    decode = AgentRuntime._decode_guided_action
    calls, final = decode('I should look first.\nACTION: {"name":"list_files","arguments":{}}',
                          ["list_files"])
    assert final is None and calls[0]["function"]["name"] == "list_files"
    assert decode('Thinking.\nACTION: {"name":"format_disk","arguments":{}}', ["list_files"]) == ([], None)
    assert decode("FINAL: done. ACTION: none", ["list_files"]) == ([], "done. ACTION: none")


@pytest.mark.parametrize("options", [
    {"harness_mode": "telepathy"}, {"max_seconds": 0}, {"max_tokens": 0},
    {"max_tool_calls": -1}, {"repair_attempts": 9},
])
def test_invalid_policies_are_rejected(options: dict) -> None:
    with pytest.raises(ValueError):
        RunPolicy(**options)


def test_native_fenced_json_tool_call_is_repaired_not_accepted_as_final(tmp_path: Path) -> None:
    """Live finding (qwen2.5-coder:14b): native-harness tool calls written as fenced JSON."""
    fenced = '```json\n{"name": "list_files", "arguments": {"path": "."}}\n```'
    native_call = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "list_files", "arguments": {"path": "."}}}]}}
    client = Client([_message(fenced), native_call, _message("Listed the files.")])
    result = AgentRuntime(client).run(
        "m:1", _profile("native_tools"), "task", WorkspaceTools(tmp_path),
        policy=RunPolicy(harness_mode="native_tools", repair_attempts=1))
    assert result.status == "completed" and result.final_message == "Listed the files."
    assert result.usage["repairs"] == 1 and result.usage["tool_calls"] == 1


def test_native_fenced_json_that_is_not_a_tool_call_is_a_valid_answer(tmp_path: Path) -> None:
    answer = '```json\n{"name": "Ada", "role": "engineer"}\n```'
    client = Client([_message(answer)])
    result = AgentRuntime(client).run(
        "m:1", _profile("native_tools"), "task", WorkspaceTools(tmp_path),
        policy=RunPolicy(harness_mode="native_tools", repair_attempts=1))
    assert result.status == "completed" and result.usage["repairs"] == 0

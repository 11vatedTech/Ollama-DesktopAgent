from __future__ import annotations

from typing import Any

from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile


class BatchClient:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({
            "model": model,
            "messages": [dict(message) for message in messages],
            "kwargs": kwargs,
        })
        return self.responses.pop(0)


def _profile() -> ModelProfile:
    return ModelProfile(
        model_name="local:latest", digest="digest", generated_at="now",
        professional_title="Workflow Operations Specialist",
        primary_specialization="Controlled tool execution.",
        agentability_mode="native_tools",
    )


def _write_calls(count: int) -> list[dict[str, Any]]:
    return [
        {
            "function": {
                "name": "write_file",
                "arguments": {"path": f"file-{index}.txt", "content": f"body-{index}"},
            }
        }
        for index in range(count)
    ]


def test_runtime_executes_only_bounded_tool_batch_and_denies_deferred_calls(tmp_path) -> None:
    client = BatchClient(
        {"message": {"role": "assistant", "tool_calls": _write_calls(10)}},
        {"message": {"role": "assistant", "content": "Finished safely."}},
    )
    workspace = tmp_path / "workspace"
    tools = WorkspaceTools(workspace)
    result = AgentRuntime(client, max_steps=3).run(
        "local:latest", _profile(), "Create the requested files.", tools
    )
    assert result.status == "completed"
    assert len(list(workspace.glob("file-*.txt"))) == 8
    assert any(event.kind == "tool_batch_limited" for event in result.trace)
    denied = [
        message for message in client.calls[1]["messages"]
        if message.get("role") == "tool" and "per-step limit" in str(message.get("content"))
    ]
    assert len(denied) == 2


def test_runtime_rejects_oversized_tool_call_batch_without_execution(tmp_path) -> None:
    client = BatchClient(
        {"message": {"role": "assistant", "tool_calls": _write_calls(33)}},
    )
    workspace = tmp_path / "workspace"
    result = AgentRuntime(client).run(
        "local:latest", _profile(), "Create some files.", WorkspaceTools(workspace)
    )
    assert result.status == "tool_call_limit"
    assert not list(workspace.glob("file-*.txt"))
    assert any(event.kind == "tool_batch_rejected" for event in result.trace)


def test_runtime_enforces_total_tool_call_budget(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(AgentRuntime, "MAX_TOTAL_TOOL_CALLS", 1)
    client = BatchClient(
        {"message": {"role": "assistant", "tool_calls": _write_calls(2)}},
        {"message": {"role": "assistant", "content": "Done."}},
    )
    workspace = tmp_path / "workspace"
    result = AgentRuntime(client, max_steps=3).run(
        "local:latest", _profile(), "Create files.", WorkspaceTools(workspace)
    )
    assert result.status == "completed"
    assert (workspace / "file-0.txt").exists()
    assert not (workspace / "file-1.txt").exists()
    assert any(event.kind == "tool_budget_exhausted" for event in result.trace)


def test_context_budget_counts_structured_tool_call_arguments(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(AgentRuntime, "MAX_CONVERSATION_CHARS", 10_000)
    large_call = {
        "function": {
            "name": "write_file",
            "arguments": {"path": "large.txt", "content": "x" * 12_000},
        }
    }
    client = BatchClient({"message": {"role": "assistant", "tool_calls": [large_call]}})
    workspace = tmp_path / "workspace"
    result = AgentRuntime(client, max_steps=3).run(
        "local:latest", _profile(), "Write a file.", WorkspaceTools(workspace)
    )
    assert result.status == "context_limit"
    assert len(client.calls) == 1


def test_reasoning_only_response_is_retried_without_reasoning(tmp_path) -> None:
    """A reasoning model can answer with analysis and no content (live H failure)."""
    client = BatchClient(
        {"message": {"role": "assistant", "content": "",
                     "thinking": "Deliberating about the task at length."}},
        {"message": {"role": "assistant", "content": "Finished after the retry."}},
    )
    workspace = tmp_path / "workspace"
    result = AgentRuntime(client, max_steps=3).run(
        "local:latest", _profile(), "Do the task.", WorkspaceTools(workspace)
    )
    assert result.status == "completed"
    assert result.final_message == "Finished after the retry."
    assert any(event.kind == "thinking_suppressed" for event in result.trace)
    assert client.calls[1]["kwargs"].get("think") is False
    # The reasoning-only turn is discarded rather than kept as an empty answer.
    roles = [message.get("role") for message in client.calls[1]["messages"]]
    assert roles.count("assistant") == 0
    # A model without reasoning text still fails closed on a truly empty answer.
    empty = BatchClient({"message": {"role": "assistant", "content": ""}})
    failed = AgentRuntime(empty, max_steps=2).run(
        "local:latest", _profile(), "Do the task.", WorkspaceTools(tmp_path / "second")
    )
    assert failed.status == "failed"
    assert "think" not in empty.calls[0]["kwargs"]


def test_undecodable_action_is_never_reported_as_a_final_answer(tmp_path) -> None:
    """Live scenario I: a half-written action was accepted as a completed answer."""
    truncated = '{"action": "tool", "name": "write_file", "arguments": {"path": "a'
    client = BatchClient(*[
        {"message": {"role": "assistant", "content": truncated}} for _ in range(6)
    ])
    profile = ModelProfile(
        model_name="local:latest", digest="digest", generated_at="now",
        professional_title="Workflow Operations Specialist",
        primary_specialization="Controlled tool execution.",
        agentability_mode="structured_actions",
    )
    workspace = tmp_path / "workspace"
    result = AgentRuntime(client, max_steps=6).run(
        "local:latest", profile, "Create the file.", WorkspaceTools(workspace)
    )
    assert result.status == "failed"
    assert "could not be decoded" in result.final_message
    assert not list(workspace.iterdir())
